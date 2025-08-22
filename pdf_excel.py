import streamlit as st
import pandas as pd
import pdfplumber
import io
import re
from collections import defaultdict

# --- ツール①：PDFからデータを抽出する関数 ---
def extract_tables_from_multiple_pdfs(pdf_files, keyword, start_page, end_page):
    all_rows = []
    if not keyword:
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
                    if keyword in text:
                        found_in_file = True
                        tables = page.extract_tables()
                        for table_index, table in enumerate(tables):
                            if not table: continue
                            all_rows.append([f"--- ページ {page.page_number} / テーブル {table_index + 1} ---"])
                            for row in table:
                                cleaned_row = ["" if item is None else str(item).replace('\n', ' ') for item in row]
                                all_rows.append(cleaned_row)
                            all_rows.append([])
        except Exception as e:
            st.error(f"ファイル「{pdf_file.name}」の処理中にエラーが発生しました: {e}", icon="🔥")
            continue
        if not found_in_file:
            st.warning(f"ファイル「{pdf_file.name}」の指定範囲では、キーワード「{keyword}」を含む表が見つかりませんでした。", icon="⚠️")
    if not any(r for r in all_rows if r): return None
    return pd.DataFrame(all_rows)

# --- ツール②：統合データを作成する関数群（元の独立アプリのロジックをそのまま使用）---
def tool2_extract_data_from_chunk(df_chunk):
    if df_chunk.empty: return None, []
    year_pat = re.compile(r"^\s*20\d{2}\s*$")
    year_cells = []
    for r in range(df_chunk.shape[0]):
        for c in range(df_chunk.shape[1]):
            cell_value = str(df_chunk.iat[r, c])
            if year_pat.match(cell_value):
                year_cells.append({"row": r, "col": c, "year": int(cell_value.strip())})
    if not year_cells: return None, []
    year_cells.sort(key=lambda x: (x['row'], x['col']))
    processed_years = set()
    initial_items = df_chunk[0].astype(str).str.strip().dropna()
    initial_items = initial_items[initial_items != ""]
    is_sonota = initial_items == 'その他'
    if is_sonota.any():
        sonota_counts = initial_items.groupby(initial_items).cumcount()
        initial_items.loc[is_sonota] = 'その他_temp_' + sonota_counts[is_sonota].astype(str)
    all_items_ordered = initial_items.drop_duplicates(keep='first').tolist()
    df_result = pd.DataFrame({'共通項目': all_items_ordered})
    for cell in year_cells:
        year = cell['year']
        if year in processed_years: continue
        processed_years.add(year)
        val_col = cell['col']
        temp_df = df_chunk.iloc[cell['row'] + 1:, [0, val_col]].copy()
        temp_df.columns = ["共通項目", year]
        temp_df["共通項目"] = temp_df["共通項目"].astype(str).str.strip()
        temp_df = temp_df[temp_df["共通項目"] != ""].dropna(subset=["共通項目"])
        is_sonota = temp_df['共通項目'] == 'その他'
        if is_sonota.any():
            sonota_counts = temp_df.groupby('共通項目').cumcount()
            temp_df.loc[is_sonota, '共通項目'] = 'その他_temp_' + sonota_counts[is_sonota].astype(str)
        temp_df[year] = pd.to_numeric(temp_df[year].astype(str).str.replace(",", ""), errors='coerce').fillna(0)
        temp_df = temp_df.drop_duplicates(subset=['共通項目'], keep='first')
        df_result = pd.merge(df_result, temp_df, on='共通項目', how='left')
    return df_result, all_items_ordered

def tool2_calculate_yoy(df):
    df_yoy = df.set_index("共通項目")
    df_yoy.index = df_yoy.index.str.replace(r'_temp_\d+$', '', regex=True)
    df_yoy = df_yoy.groupby(df_yoy.index, sort=False).sum()
    df_diff = df_yoy.diff(axis=1)
    df_diff.columns = [f"{col} 増減額" for col in df_diff.columns]
    df_pct = df_yoy.pct_change(axis=1) * 100
    df_pct.columns = [f"{col} 増減率(%)" for col in df_pct.columns]
    df_merged = pd.concat([df_yoy, df_diff, df_pct], axis=1)
    sorted_cols = []
    year_cols = sorted([col for col in df_yoy.columns if isinstance(col, int)])
    for year in year_cols:
        sorted_cols.append(year)
        if f"{year} 増減額" in df_merged.columns: sorted_cols.append(f"{year} 増減額")
        if f"{year} 増減率(%)" in df_merged.columns: sorted_cols.append(f"{year} 増減率(%)")
    return df_merged[sorted_cols].reset_index()

def process_files_and_tables(excel_file):
    try:
        xls = pd.ExcelFile(excel_file)
        sheet_name_to_read = "抽出結果" if "抽出結果" in xls.sheet_names else xls.sheet_names[0]
        df_full = pd.read_excel(xls, sheet_name=sheet_name_to_read, header=None)
    except Exception as e:
        st.error(f"Excelファイルの読み込みに失敗しました: {e}")
        return None
    
    df_full[0] = df_full[0].astype(str)
    file_indices = df_full[df_full[0].str.contains(r'ファイル名:', na=False)].index.tolist()
    file_chunks = []
    if not file_indices:
        file_chunks.append(df_full)
    else:
        for i in range(len(file_indices)):
            start_idx = file_indices[i]
            end_idx = file_indices[i+1] if i + 1 < len(file_indices) else len(df_full)
            file_chunks.append(df_full.iloc[start_idx:end_idx].reset_index(drop=True))

    grouped_tables = defaultdict(list)
    master_item_order = defaultdict(list)

    for file_chunk in file_chunks:
        page_indices = file_chunk[file_chunk[0].str.contains(r'--- ページ', na=False)].index.tolist()
        table_chunks = []
        last_idx = 0
        # ページ区切りが見つからない場合はファイル全体を一つの塊として扱う
        if not page_indices:
             clean_chunk = file_chunk[~file_chunk[0].str.contains(r'ファイル名:|---|^\s*$', na=False, regex=True)].dropna(how='all')
             if not clean_chunk.empty:
                 table_chunks.append(clean_chunk)
        else:
            # ページ区切りで分割する
            for idx in page_indices:
                chunk = file_chunk.iloc[last_idx:idx]
                if not chunk.empty:
                    table_chunks.append(chunk)
                last_idx = idx
            final_chunk = file_chunk.iloc[last_idx:]
            if not final_chunk.empty:
                table_chunks.append(final_chunk)
        
        for i, table_chunk in enumerate(table_chunks):
            # ヘッダー行などを除外
            clean_table_chunk = table_chunk[~table_chunk[0].str.contains(r'ファイル名:|---|--- ページ', na=False, regex=True)].dropna(how='all')
            if clean_table_chunk.empty: continue

            processed_df, item_order = tool2_extract_data_from_chunk(clean_table_chunk.reset_index(drop=True))
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
        if not list_of_dfs: continue
        result_df = pd.DataFrame({'共通項目': ordered_items})
        for df_to_merge in list_of_dfs:
            cols_to_drop = [col for col in df_to_merge.columns if col in result_df.columns and col != '共通項目']
            df_filtered = df_to_merge.drop(columns=cols_to_drop)
            result_df = pd.merge(result_df, df_filtered, on='共通項目', how='left')
        result_df.fillna(0, inplace=True)
        year_cols = sorted([col for col in result_df.columns if str(col).isdigit()], key=int)
        final_cols = ['共通項目'] + year_cols
        result_df = result_df[final_cols]
        for col in year_cols: result_df[col] = result_df[col].astype(int)
        result_df['共通項目'] = result_df['共通項目'].str.replace(r'_temp_\d+$', '', regex=True)
        final_summaries.append(result_df)
    return final_summaries

# --- Streamlit UIの定義 ---
st.set_page_config(page_title="多機能ツール", layout="wide")
st.info("v6.0：ツール②のロジックを元の独立アプリのものに復元しました。")
st.title("📄📊 多機能ツール")
st.write("PDFからのデータ抽出と、Excelデータの統合・分析をそれぞれ独立して行えます。")

# --- ツール①: PDF表データ抽出ツール ---
with st.container(border=True):
    st.header("ツール①：PDF表データ抽出")
    pdf_files = st.file_uploader("PDFファイルをアップロード（複数可）", type="pdf", accept_multiple_files=True, key="pdf_uploader")
    keyword = st.text_input("検索キーワード", placeholder="例: 発行済株式", key="keyword_input")
    col1, col2 = st.columns(2)
    start_page_input = col1.text_input("開始ページ", placeholder="例: 5", key="start_page")
    end_page_input = col2.text_input("終了ページ", placeholder="例: 10", key="end_page")

    if st.button("抽出開始 ▶️", key="pdf_extract_button"):
        if pdf_files:
            start_page = int(start_page_input) if start_page_input.isdigit() else None
            end_page = int(end_page_input) if end_page_input.isdigit() else None
            with st.spinner("PDFを解析中..."):
                df_result = extract_tables_from_multiple_pdfs(pdf_files, keyword, start_page, end_page)
                if df_result is not None and not df_result.empty:
                    st.success("抽出が完了しました！", icon="✅")
                    st.dataframe(df_result)
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df_result.to_excel(writer, index=False, header=False, sheet_name='抽出結果')
                    st.download_button(
                        label="📥 Excelファイルをダウンロード",
                        data=output.getvalue(),
                        file_name=f"{keyword}_抽出結果.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
        else:
            st.error("PDFファイルをアップロードしてください。", icon="🚨")

st.divider()

# --- ツール②: 統合データ作成ツール ---
with st.container(border=True):
    st.header("ツール②：統合データ作成")
    st.write("ツール①で出力したような形式のExcelファイルをアップロードして、統合・分析します。")
    excel_file = st.file_uploader("処理したいExcelファイルをアップロード", type=["xlsx"], key="excel_uploader")

    if st.button("統合まとめ表を作成 ▶️", key="excel_process_button", disabled=(excel_file is None)):
        with st.spinner("データを整理・分析中..."):
            all_summaries = process_files_and_tables(excel_file) # 元の関数を呼び出す
            if all_summaries:
                st.success(f"{len(all_summaries)}個の統合まとめ表が作成されました！", icon="✅")
                output_excel = io.BytesIO()
                with pd.ExcelWriter(output_excel, engine="xlsxwriter") as writer:
                    for i, summary_df in enumerate(all_summaries):
                        summary_df.to_excel(writer, sheet_name=f"統合まとめ表_{i+1}", index=False)
                st.download_button(
                    label="📥 全ての統合まとめ表をダウンロード",
                    data=output_excel.getvalue(),
                    file_name=f"統合まとめ表_{excel_file.name}",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                for i, summary_df in enumerate(all_summaries):
                    with st.expander(f"▼ **統合まとめ表 {i+1}** の分析結果を見る", expanded=True):
                        tab1, tab2, tab3 = st.tabs(["整理後データ", "推移グラフ", "前年比・増減"])
                        with tab1: st.dataframe(summary_df)
                        with tab2:
                            df_for_chart = summary_df.copy()
                            df_for_chart['共通項目'] = df_for_chart['共通項目'].str.replace(r'_temp_\d+$', '', regex=True)
                            df_for_chart = df_for_chart.groupby('共通項目', sort=False).sum()
                            default_items = [item for item in ["売上高", "営業利益", "経常利益", "当期純利益"] if item in df_for_chart.index]
                            selected_items = st.multiselect("グラフ表示項目", options=df_for_chart.index.tolist(), default=default_items, key=f"chart_{i}")
                            if selected_items: st.line_chart(df_for_chart.loc[selected_items].T)
                        with tab3:
                            df_yoy_result = tool2_calculate_yoy(summary_df)
                            st.dataframe(df_yoy_result.style.format(precision=2, na_rep='-'))
            elif all_summaries is not None:
                st.warning("統合できるデータが見つかりませんでした。ファイルの内容を確認してください。", icon="⚠️")
