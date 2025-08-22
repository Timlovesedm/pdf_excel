import streamlit as st
import pandas as pd
import pdfplumber
import io
import re
from collections import defaultdict

# --- ツール①：PDFからデータを抽出する関数（変更なし）---
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
            st.warning(f"ファイル「{pdf_file.name}」ではキーワードを含む表が見つかりませんでした。", icon="⚠️")
    if not any(r for r in all_rows if r): return None
    return pd.DataFrame(all_rows)

# --- ツール②：統合データを作成する関数群（年号認識を強化）---
def tool2_extract_data_from_chunk(df_chunk, year_pattern_str):
    if df_chunk.empty: return None, [], {}
    try:
        year_pat = re.compile(year_pattern_str)
    except re.error:
        st.error(f"正規表現パターン「{year_pattern_str}」が無効です。", icon="🚨")
        return None, [], {}
        
    year_cells = []
    year_map = {} # 元の列名とソート用のキーを紐付ける辞書
    for r in range(df_chunk.shape[0]):
        for c in range(df_chunk.shape[1]):
            cell_value = str(df_chunk.iat[r, c]).strip()
            if year_pat.match(cell_value):
                # パターンにマッチした文字列から数字のみを抽出してソートキーとする
                sort_key_str = "".join(filter(str.isdigit, cell_value))
                if sort_key_str:
                    sort_key = int(sort_key_str)
                    year_cells.append({"row": r, "col": c, "year_name": cell_value, "sort_key": sort_key})
                    if cell_value not in year_map:
                         year_map[cell_value] = sort_key

    if not year_cells: return None, [], {}

    year_cells.sort(key=lambda x: (x['row'], x['sort_key']))
    processed_years = set()
    initial_items = df_chunk[0].astype(str).str.strip().dropna()
    initial_items = initial_items[initial_items != ""].astype(str) # No .astype(str) before dropna
    is_sonota = initial_items == 'その他'
    if is_sonota.any():
        sonota_counts = initial_items.groupby(initial_items).cumcount()
        initial_items.loc[is_sonota] = 'その他_temp_' + sonota_counts[is_sonota].astype(str)
    all_items_ordered = initial_items.drop_duplicates(keep='first').tolist()
    df_result = pd.DataFrame({'共通項目': all_items_ordered})
    
    for cell in year_cells:
        year_name = cell['year_name']
        if year_name in processed_years: continue
        processed_years.add(year_name)
        val_col = cell['col']
        temp_df = df_chunk.iloc[cell['row'] + 1:, [0, val_col]].copy()
        temp_df.columns = ["共通項目", year_name]
        temp_df["共通項目"] = temp_df["共通項目"].astype(str).str.strip()
        temp_df = temp_df[temp_df["共通項目"] != ""].dropna(subset=["共通項目"])
        is_sonota = temp_df['共通項目'] == 'その他'
        if is_sonota.any():
            sonota_counts = temp_df.groupby('共通項目').cumcount()
            temp_df.loc[is_sonota, '共通項目'] = 'その他_temp_' + sonota_counts[is_sonota].astype(str)
        temp_df[year_name] = pd.to_numeric(temp_df[year_name].astype(str).str.replace(",", ""), errors='coerce').fillna(0)
        temp_df = temp_df.drop_duplicates(subset=['共通項目'], keep='first')
        df_result = pd.merge(df_result, temp_df, on='共通項目', how='left')
    return df_result, all_items_ordered, year_map

def tool2_calculate_yoy(df, year_map):
    df_yoy = df.set_index("共通項目")
    df_yoy.index = df_yoy.index.str.replace(r'_temp_\d+$', '', regex=True)
    df_yoy = df_yoy.groupby(df_yoy.index, sort=False).sum()
    df_diff = df_yoy.diff(axis=1)
    df_diff.columns = [f"{col} 増減額" for col in df_diff.columns]
    df_pct = df_yoy.pct_change(axis=1) * 100
    df_pct.columns = [f"{col} 増減率(%)" for col in df_pct.columns]
    df_merged = pd.concat([df_yoy, df_diff, df_pct], axis=1)
    sorted_cols = []
    # year_mapを使ってソートされた列リストを取得
    year_cols = sorted(df_yoy.columns, key=lambda col: year_map.get(col, 0))
    for year in year_cols:
        sorted_cols.append(year)
        if f"{year} 増減額" in df_merged.columns: sorted_cols.append(f"{year} 増減額")
        if f"{year} 増減率(%)" in df_merged.columns: sorted_cols.append(f"{year} 増減率(%)")
    return df_merged[sorted_cols].reset_index()

def process_files_and_tables(excel_file, year_pattern_str):
    try:
        xls = pd.ExcelFile(excel_file); sheet_name_to_read = "抽出結果" if "抽出結果" in xls.sheet_names else xls.sheet_names[0]
        df_full = pd.read_excel(xls, sheet_name=sheet_name_to_read, header=None)
    except Exception as e:
        st.error(f"Excelファイルの読み込みに失敗しました: {e}"); return None, {}
    
    df_full[0] = df_full[0].astype(str)
    file_indices = df_full[df_full[0].str.contains(r'ファイル名:', na=False)].index.tolist()
    file_chunks = [df_full] if not file_indices else [df_full.iloc[start:end].reset_index(drop=True) for start, end in zip(file_indices, file_indices[1:] + [len(df_full)])]
    
    grouped_tables, master_item_order, combined_year_map = defaultdict(list), defaultdict(list), {}
    for file_chunk in file_chunks:
        page_indices = file_chunk[file_chunk[0].str.contains(r'--- ページ', na=False)].index.tolist()
        table_chunks = []
        last_idx = 0
        if not page_indices:
             clean_chunk = file_chunk[~file_chunk[0].str.contains(r'ファイル名:|---|^\s*$', na=False, regex=True)].dropna(how='all')
             if not clean_chunk.empty: table_chunks.append(clean_chunk)
        else:
            for idx in page_indices: chunk = file_chunk.iloc[last_idx:idx]; table_chunks.append(chunk) if not chunk.empty else None; last_idx = idx
            final_chunk = file_chunk.iloc[last_idx:]; table_chunks.append(final_chunk) if not final_chunk.empty else None
        
        for i, table_chunk in enumerate(table_chunks):
            clean_table_chunk = table_chunk[~table_chunk[0].str.contains(r'ファイル名:|---|--- ページ', na=False, regex=True)].dropna(how='all')
            if clean_table_chunk.empty: continue
            processed_df, item_order, year_map = tool2_extract_data_from_chunk(clean_table_chunk.reset_index(drop=True), year_pattern_str)
            combined_year_map.update(year_map)
            if processed_df is not None and not processed_df.empty:
                grouped_tables[i].append(processed_df)
                if not master_item_order[i]: master_item_order[i].extend(item_order)
                else:
                    last_known_index = -1
                    for item in item_order:
                        if item in master_item_order[i]: last_known_index = master_item_order[i].index(item)
                        else: master_item_order[i].insert(last_known_index + 1, item); last_known_index += 1
    
    final_summaries = []
    for table_index in sorted(grouped_tables.keys()):
        list_of_dfs = grouped_tables[table_index]; ordered_items = master_item_order[table_index]
        if not list_of_dfs: continue
        result_df = pd.DataFrame({'共通項目': ordered_items})
        for df_to_merge in list_of_dfs:
            cols_to_drop = [col for col in df_to_merge.columns if col in result_df.columns and col != '共通項目']
            result_df = pd.merge(result_df, df_to_merge.drop(columns=cols_to_drop), on='共通項目', how='left')
        result_df.fillna(0, inplace=True)
        # combined_year_mapを使って列をソート
        year_cols = [col for col in result_df.columns if col in combined_year_map]
        sorted_year_cols = sorted(year_cols, key=lambda col: combined_year_map.get(col, 0))
        final_cols = ['共通項目'] + sorted_year_cols
        result_df = result_df[final_cols]
        for col in sorted_year_cols: result_df[col] = pd.to_numeric(result_df[col], errors='coerce').fillna(0)
        result_df['共通項目'] = result_df['共通項目'].str.replace(r'_temp_\d+$', '', regex=True)
        final_summaries.append(result_df)
    return final_summaries, combined_year_map

# --- Streamlit UIの定義 ---
st.set_page_config(page_title="多機能ツール", layout="wide")
st.info("v8.0：ツール②の年号/期認識を強化し、Q1や03等の形式にも対応しました。")
st.title("📄📊 多機能ツール")
st.write("PDFからのデータ抽出と、Excelデータの統合・分析をそれぞれ独立して行えます。")

# --- ツール①: PDF表データ抽出ツール ---
with st.container(border=True):
    st.header("ツール①：PDF表データ抽出")
    pdf_files = st.file_uploader("PDFファイルをアップロード（複数可）", type="pdf", accept_multiple_files=True, key="pdf_uploader")
    keyword_input_str = st.text_input("検索キーワード（複数可、カンマ区切り）", placeholder="例: 発行済株式, 自己株式, 資本金", key="keyword_input")
    col1, col2 = st.columns(2); start_page_input = col1.text_input("開始ページ", placeholder="例: 5", key="start_page"); end_page_input = col2.text_input("終了ページ", placeholder="例: 10", key="end_page")
    if st.button("抽出開始 ▶️", key="pdf_extract_button"):
        if pdf_files:
            keywords = [kw.strip() for kw in keyword_input_str.split(',') if kw.strip()]
            start_page = int(start_page_input) if start_page_input.isdigit() else None; end_page = int(end_page_input) if end_page_input.isdigit() else None
            with st.spinner("PDFを解析中..."):
                df_result = extract_tables_from_multiple_pdfs(pdf_files, keywords, start_page, end_page)
                if df_result is not None and not df_result.empty:
                    st.success("抽出が完了しました！", icon="✅"); st.dataframe(df_result)
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer: df_result.to_excel(writer, index=False, header=False, sheet_name='抽出結果')
                    st.download_button(label="📥 Excelファイルをダウンロード", data=output.getvalue(), file_name=f"複数キーワード_抽出結果.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else: st.error("PDFファイルをアップロードしてください。", icon="🚨")

st.divider()

# --- ツール②: 統合データ作成ツール ---
with st.container(border=True):
    st.header("ツール②：統合データ作成")
    st.write("ツール①で出力したような形式のExcelファイルをアップロードして、統合・分析します。")
    excel_file = st.file_uploader("処理したいExcelファイルをアップロード", type=["xlsx"], key="excel_uploader")
    year_pattern = st.text_input("年号/期を表す列ヘッダーの正規表現パターン", value=r"(.*)", help="例:`\d{2}\.\d{2}` (23.03), `\d?Q\d?` (Q1), `^\d{2,4}$` (23 or 2023)")
    if st.button("統合まとめ表を作成 ▶️", key="excel_process_button", disabled=(excel_file is None)):
        with st.spinner("データを整理・分析中..."):
            all_summaries, year_map = process_files_and_tables(excel_file, year_pattern)
            if all_summaries:
                st.success(f"{len(all_summaries)}個の統合まとめ表が作成されました！", icon="✅")
                output_excel = io.BytesIO()
                with pd.ExcelWriter(output_excel, engine="xlsxwriter") as writer:
                    for i, summary_df in enumerate(all_summaries): summary_df.to_excel(writer, sheet_name=f"統合まとめ表_{i+1}", index=False)
                st.download_button(label="📥 全ての統合まとめ表をダウンロード", data=output_excel.getvalue(), file_name=f"統合まとめ表_{excel_file.name}", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                for i, summary_df in enumerate(all_summaries):
                    with st.expander(f"▼ **統合まとめ表 {i+1}** の分析結果を見る", expanded=True):
                        tab1, tab2, tab3 = st.tabs(["整理後データ", "推移グラフ", "前年比・増減"])
                        with tab1: st.dataframe(summary_df)
                        with tab2:
                            df_for_chart = summary_df.copy(); df_for_chart['共通項目'] = df_for_chart['共通項目'].str.replace(r'_temp_\d+$', '', regex=True)
                            df_for_chart = df_for_chart.groupby('共通項目', sort=False).sum()
                            default_items = [item for item in ["売上高", "営業利益", "経常利益", "当期純利益"] if item in df_for_chart.index]
                            selected_items = st.multiselect("グラフ表示項目", options=df_for_chart.index.tolist(), default=default_items, key=f"chart_{i}")
                            if selected_items: st.line_chart(df_for_chart.loc[selected_items].T)
                        with tab3:
                            df_yoy_result = tool2_calculate_yoy(summary_df, year_map)
                            st.dataframe(df_yoy_result.style.format(precision=2, na_rep='-'))
            elif all_summaries is not None: st.warning("統合できるデータが見つかりませんでした。ファイルの内容や正規表現パターンを確認してください。", icon="⚠️")
