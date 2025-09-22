import streamlit as st
import pandas as pd
import pdfplumber
import io
import re
from collections import defaultdict

# --- ツール①：PDFからデータを抽出する関数（複数キーワード対応） ---
def extract_tables_from_multiple_pdfs(pdf_files, keywords, start_page, end_page):
    all_rows = []
    if not keywords:
        st.error("❗ キーワードが入力されていません。", icon="🚨")
        return None
    for pdf_file in pdf_files:
        all_rows.append([f"ファイル名: {pdf_file.name}"])
        all_rows.append([])
        found_in_file = False
        try:
            with pdfplumber.open(pdf_file) as pdf:
                start_index = start_page - 1 if start_page else 0
                end_index = end_page if end_page else len(pdf.pages)
                target_pages = pdf.pages[start_index:end_index]
                for page in target_pages:
                    text = page.extract_text() or ""
                    if any(kw in text for kw in keywords):
                        found_in_file = True
                        tables = page.extract_tables()
                        for table_index, table in enumerate(tables):
                            if not table:
                                continue
                            all_rows.append([f"--- ページ {page.page_number} / テーブル {table_index + 1} ---"])
                            for row in table:
                                cleaned_row = ["" if item is None else str(item).replace("\n", " ") for item in row]
                                all_rows.append(cleaned_row)
                            all_rows.append([])
        except Exception as e:
            st.error(f"ファイル「{pdf_file.name}」処理中にエラー: {e}", icon="🔥")
            continue
        if not found_in_file:
            st.warning(f"ファイル「{pdf_file.name}」ではキーワードを含む表が見つかりませんでした。", icon="⚠️")

    if not any(r for r in all_rows if r):
        return None
    return pd.DataFrame(all_rows)


# --- ツール②：統合データ作成関数（応用：「自 YYYY年MM月」形式にも対応）---
def tool2_extract_data_from_chunk(df_chunk):
    if df_chunk.empty:
        return None, []
    
    # パターン①：「(自 YYYY年MM月...」形式を認識
    from_date_pat = re.compile(r"\(自\s*(\d{4})年(\d{1,2})月")
    # パターン②：「(YYYY年MM月...」形式を認識
    date_pat = re.compile(r"\((\d{4})年(\d{1,2})月") 
    # パターン③：2024 (4桁) または 202401 (6桁) の数値を認識
    year_pat = re.compile(r"^\s*20\d{2}(\d{2})?\s*$")

    year_cells = []
    for r in range(df_chunk.shape[0]):
        for c in range(df_chunk.shape[1]):
            cell_value = str(df_chunk.iat[r, c]).strip()
            year_header = None # 見つかったヘッダーを格納する変数

            # パターン①「(自 YYYY年MM月...」を探す
            match1 = from_date_pat.search(cell_value)
            # パターン②「(YYYY年MM月...」を探す
            match2 = date_pat.search(cell_value)

            if match1:
                year = match1.group(1)  # "2021"
                month = match1.group(2) # "4"
                year_header = f"{year}/{month}" # "2021/4" という文字列を作成
            elif match2:
                year = match2.group(1)
                month = match2.group(2)
                year_header = f"{year}/{month}"
            
            # 上記パターンで見つからなければ、次にパターン③（4桁/6桁の数値）を探す
            elif cell_value.isdigit() and year_pat.match(cell_value):
                year_header = cell_value

            # ヘッダーが見つかった場合のみリストに追加
            if year_header:
                year_cells.append({"row": r, "col": c, "year_header": year_header})

    if not year_cells:
        return None, []

    year_cells.sort(key=lambda x: (x["row"], x["col"]))
    processed_years = set()
    initial_items = df_chunk[0].astype(str).str.strip().dropna()
    initial_items = initial_items[initial_items != ""]
    is_sonota = initial_items == "その他"
    if is_sonota.any():
        sonota_counts = initial_items.groupby(initial_items).cumcount()
        initial_items.loc[is_sonota] = "その他_temp_" + sonota_counts[is_sonota].astype(str)
    all_items_ordered = initial_items.drop_duplicates(keep="first").tolist()
    df_result = pd.DataFrame({"共通項目": all_items_ordered})

    for cell in year_cells:
        year_header = cell["year_header"]
        if year_header in processed_years:
            continue
        processed_years.add(year_header)
        val_col = cell["col"]
        temp_df = df_chunk.iloc[cell["row"] + 1 :, [0, val_col]].copy()
        temp_df.columns = ["共通項目", year_header]
        temp_df["共通項目"] = temp_df["共通項目"].astype(str).str.strip()
        temp_df = temp_df[temp_df["共通項目"] != ""].dropna(subset=["共通項目"])
        is_sonota = temp_df["共通項目"] == "その他"
        if is_sonota.any():
            sonota_counts = temp_df.groupby("共通項目").cumcount()
            temp_df.loc[is_sonota, "共通項目"] = "その他_temp_" + sonota_counts[is_sonota].astype(str)
        temp_df[year_header] = (
            pd.to_numeric(temp_df[year_header].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
        )
        temp_df = temp_df.drop_duplicates(subset=["共通項目"], keep="first")
        df_result = pd.merge(df_result, temp_df, on="共通項目", how="left")

    return df_result, all_items_ordered


def tool2_calculate_yoy(df):
    df_yoy = df.set_index("共通項目")
    df_yoy.index = df_yoy.index.str.replace(r"_temp_\d+$", "", regex=True)
    df_yoy = df_yoy.groupby(df_yoy.index, sort=False).sum()
    df_diff = df_yoy.diff(axis=1)
    df_diff.columns = [f"{col} 増減額" for col in df_diff.columns]
    df_pct = df_yoy.pct_change(axis=1) * 100
    df_pct.columns = [f"{col} 増減率(%)" for col in df_pct.columns]
    df_merged = pd.concat([df_yoy, df_diff, df_pct], axis=1)
    year_cols = sorted([col for col in df_yoy.columns if isinstance(col, (int, str)) and str(col).replace('/', '').isdigit()], key=lambda x: int(str(x).replace('/', '')))
    sorted_cols = []
    for year in year_cols:
        sorted_cols.append(year)
        if f"{year} 増減額" in df_merged.columns:
            sorted_cols.append(f"{year} 増減額")
        if f"{year} 増減率(%)" in df_merged.columns:
            sorted_cols.append(f"{year} 増減率(%)")
    return df_merged[sorted_cols].reset_index()


def process_files_and_tables(excel_file):
    try:
        xls = pd.ExcelFile(excel_file)
        sheet_name_to_read = "抽出結果" if "抽出結果" in xls.sheet_names else xls.sheet_names[0]
        df_full = pd.read_excel(xls, sheet_name=sheet_name_to_read, header=None)
    except Exception as e:
        st.error(f"Excelファイル読み込み失敗: {e}")
        return None

    df_full[0] = df_full[0].astype(str)
    file_indices = df_full[df_full[0].str.contains(r"ファイル名:", na=False)].index.tolist()
    file_chunks = []
    if not file_indices:
        file_chunks.append(df_full)
    else:
        for i in range(len(file_indices)):
            start_idx = file_indices[i]
            end_idx = file_indices[i + 1] if i + 1 < len(file_indices) else len(df_full)
            file_chunks.append(df_full.iloc[start_idx:end_idx].reset_index(drop=True))

    grouped_tables = defaultdict(list)
    master_item_order = defaultdict(list)

    for file_chunk in file_chunks:
        page_indices = file_chunk[file_chunk[0].str.contains(r"--- ページ", na=False)].index.tolist()
        table_chunks = []
        last_idx = 0
        if not page_indices:
            clean_chunk = file_chunk[
                ~file_chunk[0].str.contains(r"ファイル名:|---|^\s*$", na=False, regex=True)
            ].dropna(how="all")
            if not clean_chunk.empty:
                table_chunks.append(clean_chunk)
        else:
            for idx in page_indices:
                chunk = file_chunk.iloc[last_idx:idx]
                if not chunk.empty:
                    table_chunks.append(chunk)
                last_idx = idx
            final_chunk = file_chunk.iloc[last_idx:]
            if not final_chunk.empty:
                table_chunks.append(final_chunk)

        for i, table_chunk in enumerate(table_chunks):
            clean_table_chunk = table_chunk[
                ~table_chunk[0].str.contains(r"ファイル名:|---", na=False, regex=True)
            ].dropna(how="all")
            if clean_table_chunk.empty:
                continue
            processed_df, item_order = tool2_extract_data_from_chunk(
                clean_table_chunk.reset_index(drop=True)
            )
            if processed_df is not None and not processed_df.empty:
                grouped_tables[i].append(processed_df)
                current_master_order = master_item_order[i]
                if not current_master_order:
                    master_item_order[i].extend(item_order)
                else:
                    last_known_index = -1
                    for item in item_order:
                        if item in current_master_order:
                            last_known_index = current_master_order.index(item)
                        else:
                            current_master_order.insert(last_known_index + 1, item)
                            last_known_index += 1

    final_summaries = []
    for table_index in sorted(grouped_tables.keys()):
        list_of_dfs = grouped_tables[table_index]
        ordered_items = master_item_order[table_index]
        if not list_of_dfs:
            continue
        result_df = pd.DataFrame({"共通項目": ordered_items})
        for df_to_merge in list_of_dfs:
            cols_to_drop = [
                col for col in df_to_merge.columns if col in result_df.columns and col != "共通項目"
            ]
            result_df = pd.merge(
                result_df, df_to_merge.drop(columns=cols_to_drop), on="共通項目", how="left"
            )
        result_df.fillna(0, inplace=True)
        year_cols = sorted(
            [col for col in result_df.columns if col != "共通項目"],
            key=lambda x: int(str(x).replace('/', '').ljust(6, '0'))
        )
        final_cols = ["共通項目"] + year_cols
        result_df = result_df[final_cols]
        for col in year_cols:
            result_df[col] = pd.to_numeric(result_df[col], errors='coerce').fillna(0).astype(int)
        result_df["共通項目"] = result_df["共通項目"].str.replace(r"_temp_\d+$", "", regex=True)
        final_summaries.append(result_df)
    return final_summaries


# --- Streamlit UI ---
st.set_page_config(page_title="多機能ツール", layout="wide")
st.title("📄📊 多機能ツール")

# --- ツール① ---
with st.container(border=True):
    st.header("ツール①：PDF表データ抽出")
    pdf_files = st.file_uploader(
        "PDFファイルをアップロード（複数可）", type="pdf", accept_multiple_files=True
    )
    keyword_input_str = st.text_input("検索キーワード（カンマ区切り）")
    col1, col2 = st.columns(2)
    start_page_input = col1.text_input("開始ページ", placeholder="例: 5")
    end_page_input = col2.text_input("終了ページ", placeholder="例: 10")
    if st.button("抽出開始 ▶️"):
        if pdf_files:
            keywords = [kw.strip() for kw in keyword_input_str.split(",") if kw.strip()]
            start_page = int(start_page_input) if start_page_input.isdigit() else None
            end_page = int(end_page_input) if end_page_input.isdigit() else None
            with st.spinner("PDF解析中..."):
                df_result = extract_tables_from_multiple_pdfs(
                    pdf_files, keywords, start_page, end_page
                )
                if df_result is not None and not df_result.empty:
                    st.success("抽出完了！", icon="✅")
                    st.dataframe(df_result)
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                        df_result.to_excel(writer, index=False, header=False, sheet_name="抽出結果")
                        workbook = writer.book
                        worksheet = writer.sheets["抽出結果"]
                        bold_format = workbook.add_format({"bold": True, "font_size": 20})
                        for idx, val in enumerate(df_result[0]):
                            if isinstance(val, str) and val.startswith("ファイル名:"):
                                worksheet.set_row(idx, None, bold_format)
                    
                    if keywords:
                        base_name = '_'.join(keywords)
                        download_filename = f"{base_name}_まとめ.xlsx"
                    else:
                        download_filename = "抽出結果_まとめ.xlsx"

                    st.download_button(
                        label="📥 Excelファイルをダウンロード",
                        data=output.getvalue(),
                        file_name=download_filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
        else:
            st.error("PDFファイルをアップロードしてください。", icon="🚨")

st.divider()

# --- ツール② ---
with st.container(border=True):
    st.header("ツール②：統合データ作成")
    excel_file = st.file_uploader("Excelファイルをアップロード", type=["xlsx"])
    if st.button("統合まとめ表を作成 ▶️", disabled=(excel_file is None)):
        with st.spinner("データ整理中..."):
            all_summaries = process_files_and_tables(excel_file)
            if all_summaries:
                st.success(f"{len(all_summaries)}個のまとめ表を作成！", icon="✅")
                output_excel = io.BytesIO()
                with pd.ExcelWriter(output_excel, engine="xlsxwriter") as writer:
                    for i, summary_df in enumerate(all_summaries):
                        summary_df.to_excel(
                            writer, sheet_name=f"統合まとめ表_{i+1}", index=False
                        )

                base_name_input = excel_file.name.rsplit('.xlsx', 1)[0]
                if base_name_input.endswith('_まとめ'):
                    base_name_output = base_name_input.removesuffix('_まとめ') + '_統合'
                else:
                    base_name_output = base_name_input + '_統合'
                download_filename = f"{base_name_output}.xlsx"

                st.download_button(
                    label="📥 統合まとめ表をダウンロード",
                    data=output_excel.getvalue(),
                    file_name=download_filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
