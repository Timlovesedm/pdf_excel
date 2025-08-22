import streamlit as st
import pandas as pd
import pdfplumber
import io
import re
from collections import defaultdict

# --- アプリ1の関数: PDFからデータを抽出 ---
def extract_tables_from_multiple_pdfs(pdf_files, keyword, start_page, end_page):
    """
    複数のPDFファイルからキーワードを含む表を抽出し、一つのDataFrameにまとめる
    ページ範囲指定にも対応
    """
    all_rows = []

    if not keyword:
        st.error("❗ キーワードが入力されていません。")
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
                    page_number = page.page_number
                    text = page.extract_text() or ""
                    if keyword in text:
                        found_in_file = True
                        tables = page.extract_tables()
                        for table_index, table in enumerate(tables):
                            if not table:
                                continue
                            all_rows.append([f"--- ページ {page_number} / テーブル {table_index + 1} ---"])
                            for row in table:
                                cleaned_row = ["" if item is None else str(item).replace('\n', ' ') for item in row]
                                all_rows.append(cleaned_row)
                            all_rows.append([])
        except Exception as e:
            st.error(f"ファイル「{pdf_file.name}」の処理中にエラーが発生しました: {e}")
            continue

        if not found_in_file:
            st.warning(f"ファイル「{pdf_file.name}」の指定範囲では、キーワード「{keyword}」を含む表が見つかりませんでした。")

        if len(pdf_files) > 1:
            all_rows.append(['---' * 20])
            all_rows.append([])

    if not all_rows:
        return None

    # 空の行が連続しないように調整
    final_rows = []
    for i, row in enumerate(all_rows):
        if not row and (i == 0 or not final_rows[-1]):
            continue
        final_rows.append(row)

    return pd.DataFrame(final_rows)


# --- アプリ2の関数群: データの整理・分析 ---

def extract_data_from_chunk(df_chunk):
    """
    単一の表ブロック(DataFrame)を受け取り、3つのルールに従ってデータを抽出する。
    """
    if df_chunk.empty:
        return None, []

    year_pat = re.compile(r"^\s*20\d{2}\s*$")
    year_cells = []
    for r in range(df_chunk.shape[0]):
        for c in range(df_chunk.shape[1]):
            cell_value = df_chunk.iat[r, c]
            if pd.notna(cell_value) and bool(year_pat.match(str(cell_value))):
                year_cells.append({"row": r, "col": c, "year": int(str(cell_value).strip())})

    if not year_cells:
        return None, []

    year_cells.sort(key=lambda x: (x['row'], x['col']))
    processed_years = set()
    initial_items = df_chunk[0].astype(str).str.strip().dropna()
    initial_items = initial_items[initial_items != ""]
    is_sonota = initial_items == 'その他'
    if is_sonota.any():
        sonota_counts = initial_items.groupby(initial_items).cumcount()
        initial_items.loc[is_sonota] = initial_items.loc[is_sonota] + '_temp_' + sonota_counts[is_sonota].astype(str)
    all_items_ordered = initial_items.drop_duplicates(keep='first').tolist()
    df_result = pd.DataFrame({'共通項目': all_items_ordered})

    for cell in year_cells:
        year = cell['year']
        if year in processed_years:
            continue
        processed_years.add(year)
        val_col = cell['col']
        temp_df = df_chunk.iloc[cell['row'] + 1:, [0, val_col]].copy()
        temp_df.columns = ["共通項目", year]
        temp_df["共通項目"] = temp_df["共通項目"].astype(str).str.strip()
        temp_df.dropna(subset=["共通項目"], inplace=True)
        temp_df = temp_df[temp_df["共通項目"] != ""]
        is_sonota = temp_df['共通項目'] == 'その他'
        if is_sonota.any():
            sonota_counts = temp_df.groupby('共通項目').cumcount()
            temp_df.loc[is_sonota, '共通項目'] = temp_df.loc[is_sonota, '共通項目'] + '_temp_' + sonota_counts[is_sonota].astype(str)
        temp_df[year] = pd.to_numeric(temp_df[year].astype(str).str.replace(",", ""), errors='coerce').fillna(0)
        temp_df = temp_df.drop_duplicates(subset=['共通項目'], keep='first')
        df_result = pd.merge(df_result, temp_df, on='共通項目', how='left')

    return df_result, all_items_ordered


def calculate_yoy(df):
    """前年比と増減額を計算する"""
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
        if f"{year} 増減額" in df_merged.columns:
            sorted_cols.append(f"{year} 増減額")
        if f"{year} 増減率(%)" in df_merged.columns:
            sorted_cols.append(f"{year} 増減率(%)")
    df_merged = df_merged[sorted_cols].reset_index()
    return df_merged


def process_extracted_data(df_full):
    """
    抽出されたDataFrameを受け取り、統合まとめ表を作成する
    （ファイル読み込み部分を削除し、DataFrameを直接受け取るように変更）
    """
    if df_full is None or df_full.empty:
        st.error("処理対象のデータがありません。")
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
        # '--- ページ' を含む行のインデックスを取得し、それを元に表を分割
        page_indices = file_chunk[file_chunk[0].str.contains(r'--- ページ', na=False)].index.tolist()
        
        # 表の塊（チャンク）を格納するリスト
        table_chunks = []
        if not page_indices:
            # ページ区切りがない場合は、ファイル全体を一つの塊として扱う
            # （ファイル名ヘッダーなどを除外）
            clean_chunk = file_chunk[~file_chunk[0].str.contains(r'ファイル名:|---|^\s*$', na=False, regex=True)].dropna(how='all')
            if not clean_chunk.empty:
                table_chunks.append(clean_chunk)
        else:
            # ページ区切りで分割
            last_end_idx = 0
            for i in range(len(page_indices)):
                start_idx = page_indices[i]
                end_idx = page_indices[i+1] if i+1 < len(page_indices) else len(file_chunk)
                # 表データのみを抽出（ヘッダー行の次から次のヘッダー行の前まで）
                chunk = file_chunk.iloc[start_idx + 1:end_idx]
                clean_chunk = chunk[~chunk[0].str.contains(r'ファイル名:|---|^\s*$', na=False, regex=True)].dropna(how='all')
                if not clean_chunk.empty:
                    table_chunks.append(clean_chunk)

        for i, table_chunk in enumerate(table_chunks):
            processed_df, item_order = extract_data_from_chunk(table_chunk.reset_index(drop=True))
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
        year_cols = [col for col in result_df.columns if col != '共通項目']
        sorted_year_cols = sorted([col for col in year_cols if isinstance(col, (int, float)) or str(col).isdigit()], key=int)
        final_cols = ['共通項目'] + sorted_year_cols
        result_df = result_df[final_cols]
        for col in sorted_year_cols:
            result_df[col] = result_df[col].astype(int)
        result_df['共通項目'] = result_df['共通項目'].str.replace(r'_temp_\d+$', '', regex=True)
        final_summaries.append(result_df)
    return final_summaries


# --- Streamlit UI 部分 ---

st.set_page_config(page_title="PDFデータ抽出・統合分析ツール", layout="wide")
st.title("📄 PDFデータ抽出・統合分析ツール 📊")
st.write("PDFからキーワードで表を抽出し、複数のファイルから同じ順番の表を統合・分析します。")
st.divider()

# --- セッション状態の初期化 ---
if 'extracted_data' not in st.session_state:
    st.session_state.extracted_data = None
if 'analysis_results' not in st.session_state:
    st.session_state.analysis_results = None
if 'pdf_file_names' not in st.session_state:
    st.session_state.pdf_file_names = "data"


# --- ステップ1: PDFからのデータ抽出 ---
st.header("ステップ1: PDFから表データを抽出")

with st.container(border=True):
    uploaded_files = st.file_uploader(
        "PDFファイルをアップロード（複数選択可）",
        type="pdf",
        accept_multiple_files=True
    )
    keyword = st.text_input("検索キーワードを入力", placeholder="例: 発行済株式")
    col1, col2 = st.columns(2)
    with col1:
        start_page_input = st.text_input("開始ページ", placeholder="未入力の場合は全ページ")
    with col2:
        end_page_input = st.text_input("終了ページ", placeholder="未入力の場合は最後まで")

    if st.button("抽出開始 ▶️", type="primary"):
        start_page = int(start_page_input) if start_page_input.isdigit() else None
        end_page = int(end_page_input) if end_page_input.isdigit() else None

        if uploaded_files:
            with st.spinner("PDFを解析中..."):
                df_result = extract_tables_from_multiple_pdfs(uploaded_files, keyword, start_page, end_page)
                st.session_state.extracted_data = df_result
                st.session_state.analysis_results = None # 新しい抽出なので分析結果をリセット
                if df_result is not None and not df_result.empty:
                    st.session_state.pdf_file_names = "_".join([f.name.split('.')[0] for f in uploaded_files])
                    st.success("✅ 抽出が完了しました！下にプレビューと分析ステップが表示されます。")
                else:
                    st.warning("指定された条件で抽出できるデータが見つかりませんでした。")
        else:
            st.error("❗ PDFファイルをアップロードしてください。")

# --- 抽出結果の表示とステップ2への誘導 ---
if st.session_state.extracted_data is not None:
    st.subheader("抽出結果プレビュー")
    st.dataframe(st.session_state.extracted_data.head(20)) # 長い場合に備えて先頭のみ表示

    # 抽出結果ダウンロード
    output_excel_extracted = io.BytesIO()
    with pd.ExcelWriter(output_excel_extracted, engine='xlsxwriter') as writer:
        st.session_state.extracted_data.to_excel(writer, index=False, header=False, sheet_name='抽出結果')

    st.download_button(
        label="📥 抽出結果全体をExcelでダウンロード",
        data=output_excel_extracted.getvalue(),
        file_name=f"{keyword}_抽出結果.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.divider()

    # --- ステップ2: 統合まとめ表の作成・分析 ---
    st.header("ステップ2: 統合データを作成・分析")
    st.write("抽出結果から、複数のPDFにまたがる同じ順番の表を一つにまとめ、分析します。")

    if st.button("統合まとめ表を作成 ▶️", type="primary"):
        with st.spinner("データを整理・分析中..."):
            all_summaries = process_extracted_data(st.session_state.extracted_data)
            st.session_state.analysis_results = all_summaries

    # --- 分析結果の表示 ---
    if st.session_state.analysis_results is not None:
        if st.session_state.analysis_results:
            st.success(f"✅ {len(st.session_state.analysis_results)}個の統合まとめ表が作成されました！")

            # 統合結果一括ダウンロード
            output_excel_summary = io.BytesIO()
            with pd.ExcelWriter(output_excel_summary, engine="xlsxwriter") as writer:
                for i, summary_df in enumerate(st.session_state.analysis_results):
                    summary_df.to_excel(writer, sheet_name=f"統合まとめ表_{i+1}", index=False)

            st.download_button(
                label="📥 全ての統合まとめ表をExcelで一括ダウンロード",
                data=output_excel_summary.getvalue(),
                file_name=f"統合まとめ表_{st.session_state.pdf_file_names}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.divider()

            # 各まとめ表の詳細表示
            for i, summary_df in enumerate(st.session_state.analysis_results):
                with st.expander(f"▼ **統合まとめ表 {i+1}** の詳細と分析結果を見る"):
                    tab1, tab2, tab3 = st.tabs(["📊 整理後データ", "📈 推移グラフ", "🆚 前年比・増減"])

                    with tab1:
                        st.dataframe(summary_df)

                    with tab2:
                        st.subheader("主要項目の年度推移グラフ")
                        df_for_chart = summary_df.copy()
                        df_for_chart['共通項目'] = df_for_chart['共通項目'].str.replace(r'_temp_\d+$', '', regex=True)
                        df_for_chart = df_for_chart.groupby('共通項目', sort=False).sum()
                        items = df_for_chart.index.tolist()
                        default_items = [item for item in ["売上高", "営業利益", "経常利益", "当期純利益"] if item in items]
                        selected_items = st.multiselect(
                            "グラフに表示する項目を選択", options=items, default=default_items, key=f"chart_{i}"
                        )
                        if selected_items:
                            st.line_chart(df_for_chart.loc[selected_items].T)

                    with tab3:
                        st.subheader("前年比・増減額")
                        df_yoy_result = calculate_yoy(summary_df)
                        st.dataframe(df_yoy_result.style.format(precision=2, na_rep='-'))
        else:
            st.warning("統合できるデータが見つかりませんでした。抽出されたデータに年号（例: 2023）が含まれているか、表の構造が正しいかを確認してください。")