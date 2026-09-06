import streamlit as st
import pandas as pd
import base64
import datetime
from supabase import create_client, Client

# 初始化 Supabase 连接
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

SHOPS = ["大号", "小号"]
STATUSES = ["买家已下单", "我方已下单", "已合包", "已到货", "已发货"]

st.set_page_config(page_title="图书销售云后台", layout="wide")
st.set_page_config(page_title="图书销售云后台", layout="wide")

# 🎨 注入现代化 UI 样式：卡片化圆角、精美阴影、优化选项卡与表格质感
st.markdown("""
<style>
    /* 全局背景微调 */
    .stApp {
        background-color: #f8fafc;
    }
    
    /* 表单区域容器化：白色圆角卡片 + 细边框 + 微阴影 */
    div[data-testid="stForm"] {
        background-color: #ffffff;
        padding: 24px;
        border-radius: 16px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
    }
    
    /* 顶部指标卡片美化 */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        padding: 16px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.02);
    }
    
    /* 优化选项卡 Tab 样式：更具现代感 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background-color: #f1f5f9;
        padding: 6px;
        border-radius: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 40px;
        border-radius: 8px;
        font-weight: 500;
        color: #475569;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ffffff !important;
        color: #0f172a !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    
    /* 调整分割线更柔和 */
    hr {
        margin: 1.5rem 0;
        border-color: #e2e8f0;
    }
</style>
""", unsafe_allow_html=True)
st.title("☁️ 图书后台 ")

# 读取云端数据（默认按下单时间：最早的排在最前面）
@st.cache_data(ttl=2) 
def load_data():
    response = supabase.table("orders").select("*").execute()
    df = pd.DataFrame(response.data)
    if not df.empty and "order_time" in df.columns:
        df["order_time"] = pd.to_datetime(df["order_time"], errors="coerce")
        df = df.sort_values(by="order_time", ascending=True) # 时间最早排前面
    return df

df = load_data()

# ==================== 手机超友好的 6 大功能下拉菜单导航 ====================
st.markdown("### 📚 图书后台管理系统")

menu_options = [
    "📝 常规录入", 
    "📋 订单总览", 
    "🔮 预售管理", 
    "🚚 发货看板", 
    "📦 包裹合拼与运费", 
    "📊 月度营收统计"
]

selected_tab = st.selectbox("📌 请选择功能页面", menu_options, label_visibility="collapsed")
st.write("---")

tab1 = (selected_tab == "📝 常规录入")
tab2 = (selected_tab == "📋 订单总览")
tab3 = (selected_tab == "🔮 预售管理")
tab4 = (selected_tab == "🚚 发货看板")
tab5 = (selected_tab == "📦 包裹合拼与运费")
tab6 = (selected_tab == "📊 月度营收统计")

# ==================== TAB 1: 常规单笔录入 (顶部自带截图自动识别填单 + 互斥联动) ====================
if tab1:
    st.markdown("##### 📝 录入买家买书需求 (默认合拼订单)")
    st.write("") 

    # 0. 初始化 session_state 默认值
    for k, default_val in [("t1_buyer", ""), ("t1_xianyu", ""), ("t1_manual_book", ""), ("t1_history_book", "-- 手动输入新书名 / 或从下方选择 --"), ("t1_price_editable", 0.0)]:
        if k not in st.session_state:
            st.session_state[k] = default_val

    if st.session_state.get("should_clear_t1", False):
        st.session_state["t1_buyer"] = ""
        st.session_state["t1_xianyu"] = ""
        st.session_state["t1_manual_book"] = ""
        st.session_state["t1_history_book"] = "-- 手动输入新书名 / 或从下方选择 --"
        st.session_state["t1_price_editable"] = 0.0
        st.session_state["should_clear_t1"] = False

 # ==================== 📸 顶部：闲鱼截图智能识别 (优化版：精准提取买家昵称、下单时间、价格、单号) ====================
    with st.container():
        st.markdown("##### 📸 闲鱼截图智能识别 (自动提取文字并填入表单)")
        uploaded_screenshot = st.file_uploader("上传闲鱼订单截图", type=["jpg", "jpeg", "png"], key="auto_screenshot_input")
        
        if uploaded_screenshot is not None:
            st.image(uploaded_screenshot, width=200, caption="已上传待识别截图")
            if st.button("✨ 开始提取图片文字并填充", type="primary", key="parse_img_btn"):
                try:
                    import pytesseract
                    from PIL import Image
                    import re

                    image = Image.open(uploaded_screenshot)
                    extracted_text = pytesseract.image_to_string(image, lang='chi_sim+eng')
                    
                    # 🔍 优化后的正则匹配逻辑：
                    # 1. 提取买家账号/昵称：“买家昵称”后面紧跟的文字
                    buyer_match = re.search(r'买家昵称\s*[:：]?\s*([^\n\r]+)', extracted_text)
                    detected_buyer = buyer_match.group(1).strip() if buyer_match else ""
                    
                    # 2. 提取下单日期与时间：“下单时间”后面的完整时间格式 (如 2026-09-05 23:03:41)
                    time_match = re.search(r'下单时间\s*[:：]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{2}:\d{2}:\d{2})', extracted_text)
                    detected_datetime_str = time_match.group(1).strip() if time_match else ""
                    
                    # 3. 提取价格（匹配 ¥ 后面的数字）
                    prices = re.findall(r'[¥￥]\s*(\d+\.\d{2})', extracted_text)
                    detected_price = float(prices[0]) if prices else 0.0
                    
                    # 4. 提取闲鱼订单编号（15到20位长数字）
                    numbers = re.findall(r'\b\d{15,20}\b', extracted_text)
                    detected_xianyu = numbers[0] if numbers else ""
                    
                    # 💡 写入状态自动回填到表单变量中
                    if detected_buyer:
                        st.session_state["t1_buyer"] = detected_buyer
                    if detected_price > 0:
                        st.session_state["t1_price_editable"] = detected_price
                    if detected_xianyu:
                        st.session_state["t1_xianyu"] = detected_xianyu
                        
                    # 如果成功解析出下单时间，尝试拆分出日期和具体时间填入对应的控件
                    if detected_datetime_str:
                        try:
                            dt_obj = pd.to_datetime(detected_datetime_str)
                            st.session_state["t1_date"] = dt_obj.date()
                            st.session_state["t1_time"] = dt_obj.time()
                        except:
                            pass
                        
                    st.success(f"🎉 识别成功！\n- 买家: {detected_buyer or '未识别'}\n- 价格: ¥{detected_price}\n- 单号: {detected_xianyu or '未识别'}\n- 下单时间: {detected_datetime_str or '未识别'}")
                    import time
                    time.sleep(0.8)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 识别失败，错误信息: {e}")

    # 区分现货或预售
    stock_type = st.radio("📦 商品属性", ["现货", "预售"], index=0, horizontal=True, key="t1_stock_type")
    st.write("---")
    
    # 📚 智能提取历史书名字典、完整版本价格映射、预售时间及图片
    existing_books = []
    book_default_cutoff = {}
    book_default_shipping = {}
    exact_book_price = {}       
    base_book_price = {}        
    book_default_image = {}
    
    if not df.empty and "book_name" in df.columns:
        for _, row in df.iterrows():
            b_raw = str(row.get("book_name", ""))
            p_val = row.get("price_sell", 0.0)
            img_val = row.get("book_image", "")
            if b_raw and b_raw != "nan":
                base_name = b_raw.split("（")[0].split("(")[0].strip()
                if base_name:
                    existing_books.append(base_name)
                    if p_val and float(p_val) > 0:
                        exact_book_price[b_raw] = float(p_val)          
                        base_book_price[base_name] = float(p_val)       
                        
                    if img_val and str(img_val).startswith("data:image"):
                        book_default_image[base_name] = img_val
                    cutoff_val = row.get("official_cutoff_time")
                    shipping_val = row.get("official_shipping_time")
                    if cutoff_val: book_default_cutoff[base_name] = cutoff_val
                    if shipping_val: book_default_shipping[base_name] = shipping_val
                    
        existing_books = sorted(list(set(existing_books)))
    
    # 🎯 互斥联动逻辑控制
    current_history = st.session_state.get("t1_history_book", "-- 手动输入新书名 / 或从下方选择 --")
    current_manual = st.session_state.get("t1_manual_book", "")

    if current_manual.strip():
        if current_history != "-- 手动输入新书名 / 或从下方选择 --":
            st.session_state["t1_history_book"] = "-- 手动输入新书名 / 或从下方选择 --"
            current_history = "-- 手动输入新书名 / 或从下方选择 --"

    # 🔴 待选择/未选择状态判定（上下皆空）
    is_unselected = (current_history == "-- 手动输入新书名 / 或从下方选择 --") and (not current_manual.strip())

    if is_unselected:
        st.markdown("""
            <style>
            div[data-baseweb="select"] > div { border: 2px solid #ff4b4b !important; background-color: #fff8f8; }
            input[aria-label*="手动输入/补充书名"] { border: 2px solid #ff4b4b !important; background-color: #fff8f8 !important; }
            </style>
        """, unsafe_allow_html=True)
        st.error("⚠️ 【必填提醒】请从下方历史下拉框选择一本书，或者在下方手动输入新书名！")

    c1, c2, c3 = st.columns(3)
    with c1:
        buyer = st.text_input("1. 买家账号", key="t1_buyer")
        xianyu = st.text_input("2. 闲鱼单号 (选填)", key="t1_xianyu")
        
        st.markdown("---")
        st.markdown("📖 **书名选择**")
        
        selected_history_book = st.selectbox(
            "从历史书名中快速选择 (点击下拉选择)", 
            ["-- 手动输入新书名 / 或从下方选择 --"] + existing_books,
            key="t1_history_book",
            disabled=bool(current_manual.strip())
        )
        
        manual_book = st.text_input(
            "或者手动输入/补充书名 (可填 A+B 合并)", 
            key="t1_manual_book",
            disabled=bool(selected_history_book != "-- 手动输入新书名 / 或从下方选择 --")
        )
        
        if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --":
            base_book = selected_history_book
            is_history_selected = True
        else:
            base_book = manual_book.split("（")[0].split("(")[0].strip() if manual_book else ""
            is_history_selected = False

    with c2:
        shop = st.selectbox("4. 下单店铺", SHOPS, key="t1_shop")
        status = st.selectbox("5. 当前订单状态", STATUSES, key="t1_status")
        
    with c3:
        input_date = st.date_input("7. 买家下单日期", value=datetime.date.today(), key="t1_date")
        input_time = st.time_input("8. 买家下单时间", value=datetime.datetime.now().time(), key="t1_time")
        
        auto_deadline = input_date + datetime.timedelta(days=15)
        st.info(f"⏰ 发货截止日期 (自动+15天): **{auto_deadline.strftime('%Y-%m-%d')}**")
        
        st.markdown("---")
        edition_choice = st.radio(
            "✨ 特装/版本选项",
            ["官网特", "A店特", "特装", "普装"],
            index=3,
            horizontal=True,
            key="t1_edition"
        )

    raw_base_name = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
    candidate_full_name = f"{raw_base_name}（{edition_choice}）" if raw_base_name else ""
    
    with c2:
        if candidate_full_name and candidate_full_name in exact_book_price:
            default_price = exact_book_price[candidate_full_name]
            p_sell = st.number_input(f"6. 买家下单总价 (营收 - 已同步【{edition_choice}】历史价格)", value=default_price, disabled=True, key="t1_price_locked")
            st.caption(f"🔒 已自动锁定该书【{edition_choice}】的历史同版本价格")
        elif raw_base_name and raw_base_name in base_book_price:
            default_price = base_book_price[raw_base_name]
            p_sell = st.number_input("6. 买家下单总价 (营收 - 检测到其他版本价格，可修改)", value=default_price, min_value=0.0, format="%.2f", key="t1_price_editable_with_default")
            st.caption(f"💡 提示：该书有其他版本历史价格，当前【{edition_choice}】可按需修改")
        else:
            p_sell = st.number_input("6. 买家下单总价 (营收)", value=0.0, min_value=0.0, format="%.2f", key="t1_price_editable")

    official_cutoff = ""
    official_shipping = ""
    if stock_type == "预售":
        st.markdown("---")
        st.warning("🔮 **预售商品专属信息**：已自动同步同名书籍的历史截单与发货时间")
        
        default_cutoff_date = datetime.date.today()
        default_shipping_date = datetime.date.today() + datetime.timedelta(days=30)
        
        target_book_key = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
        
        if target_book_key in book_default_cutoff:
            try:
                default_cutoff_date = pd.to_datetime(book_default_cutoff[target_book_key]).date()
            except:
                pass
        if target_book_key in book_default_shipping:
            try:
                default_shipping_date = pd.to_datetime(book_default_shipping[target_book_key]).date()
            except:
                pass
        
        pc1, pc2 = st.columns(2)
        with pc1:
            cutoff_date = st.date_input("官方截单日期", value=default_cutoff_date, key="t1_cutoff")
            official_cutoff = cutoff_date.isoformat()
        with pc2:
            shipping_date = st.date_input("预计官方发货日期", value=default_shipping_date, key="t1_shipping")
            official_shipping = shipping_date.isoformat()

    st.write("---")
    uploaded_image = st.file_uploader("📸 上传书本真实照片 (留空则自动继承历史同款照片)", type=["jpg", "jpeg", "png"], key="book_upload_t1")
    
    image_base64 = ""
    target_img_key = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
    
    if uploaded_image is not None:
        bytes_data = uploaded_image.getvalue()
        image_base64 = f"data:image/jpeg;base64,{base64.b64encode(bytes_data).decode()}"
        st.image(uploaded_image, width=120, caption="已上传新照片预览")
    elif target_img_key in book_default_image:
        image_base64 = book_default_image[target_img_key]
        st.success("🖼️ 已自动继承该书历史上传的真实照片")
    
    st.write("")
    st.markdown('<div id="top-anchor"></div>', unsafe_allow_html=True)

    if st.button("💾 保存单笔订单", type="primary", key="t1_submit_btn"):
        if is_unselected:
            st.error("❌ 请先从历史书名中选择一本书，或在下方手动输入书名！")
        elif not buyer:
            st.error("❌ 请输入买家账号！")
        else:
            real_base_name = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else manual_book.strip()
            final_book_name = f"{real_base_name}（{edition_choice}）"
            combined_datetime = datetime.datetime.combine(input_date, input_time).isoformat()
            
            supabase.table("orders").insert({
                "buyer_name": buyer,
                "xianyu_no": xianyu, 
                "book_name": final_book_name,
                "shop_name": shop,
                "status": status,
                "price_sell": p_sell,
                "price_buy": 0.0, 
                "book_image": image_base64,
                "purchase_type": "合并拼单",
                "order_time": combined_datetime,
                "stock_type": stock_type,
                "deadline": auto_deadline.isoformat(),
                "official_cutoff_time": official_cutoff,
                "official_shipping_time": official_shipping
            }).execute()
            
            st.session_state["should_clear_t1"] = True
            
            st.success(f"✅ 成功保存买家【{buyer}】的订单【{final_book_name}】！表单已清空并重置。")
            
            import streamlit.components.v1 as components
            components.html("""
                <script>
                    setTimeout(function() {
                        window.parent.scrollTo({top: 0, behavior: 'smooth'});
                    }, 50);
                </script>
            """, height=0)
            
            import time
            time.sleep(0.8)
            st.rerun()
            
            
# ====== TAB 2: 现货待下单区 (采购组包) ======
if tab2:
    st.subheader("⏳ 现货待下单区")
    st.info("💡 显示所有属性为【现货】且状态为【买家已下单】的订单。在此多选并填写总成本后一键变更为【我方已下单】。")
    
    if not df.empty:
        spot_wait_df = df[(df["status"] == "买家已下单") & (df["stock_type"] == "现货")].copy()
        
        if not spot_wait_df.empty:
            display_spot = spot_wait_df[["id", "buyer_name", "book_name", "price_sell", "deadline", "order_time"]].copy()
            display_spot.columns = ["订单编号", "买家账号", "书名", "买家下单价", "发货截止日期", "下单时间"]
            display_spot.insert(0, "选择下单", False)
            
            edited_spot = st.data_editor(
                display_spot,
                column_config={"选择下单": st.column_config.CheckboxColumn("勾选打包", default=False)},
                disabled=["订单编号", "买家账号", "书名", "买家下单价", "发货截止日期", "下单时间"],
                use_container_width=True,
                key="spot_wait_editor"
            )
            
            selected_spot = edited_spot[edited_spot["选择下单"] == True]
            if not selected_spot.empty:
                with st.form("spot_purchase_form"):
                    total_cost = st.number_input("这批现货的【我方总采购成本】", min_value=0.0, format="%.2f")
                    if st.form_submit_button("⚡ 确认现货已下单并平摊成本", type="primary"):
                        s_ids = selected_spot["订单编号"].tolist()
                        split_c = total_cost / len(s_ids) if len(s_ids) > 0 else 0.0
                        for oid in s_ids:
                            supabase.table("orders").update({"status": "我方已下单", "price_buy": split_c}).eq("id", int(oid)).execute()
                        st.success(f"✅ 成功更新 {len(s_ids)} 笔现货订单状态为【我方已下单】！")
                        st.rerun()
        else:
            st.success("🎉 当前没有等待下单的现货订单。")
    else:
        st.info("暂无数据。")

# ====== TAB 3: 预售专区 (相同书名汇总与待下单统计) ======
if tab3:
    st.subheader("🔮 预售专区 (同款预售书汇总与截单跟踪)")
    st.info("💡 系统已自动将相同书名的预售需求进行汇总。最前方会显示该书【还有多少本等待我去下单】，方便你统一去供应商处采购！")
    
    if not df.empty:
        # 🎯 只筛选预售且状态为“买家已下单”的未采购需求进行汇总展示
        presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].copy()
        
        if not presale_wait_df.empty:
            # 按书名、官方截单时间、预计发货时间进行分组汇总
            group_cols = ["book_name", "official_cutoff_time", "official_shipping_time"]
            
            presale_summary = presale_wait_df.groupby(group_cols).agg(
                待下单数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            # 按照待下单数量从多到少排序
            presale_summary = presale_summary.sort_values(by="待下单数量", ascending=False)
            
            # 重命名列
            presale_summary = presale_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 官方截单时间",
                "official_shipping_time": "🚚 预计官方发货时间",
                "待下单数量": "🔥 还有几本待下单"
            })
            
            # 在最前面插入勾选框
            presale_summary.insert(0, "选择下单", False)
            
            cols_order = ["选择下单", "🔥 还有几本待下单", "📖 预售书名", "⏰ 官方截单时间", "🚚 预计官方发货时间", "买家列表"]
            available_pre_cols = [c for c in cols_order if c in presale_summary.columns]
            
            edited_presale = st.data_editor(
                presale_summary[available_pre_cols],
                column_config={
                    "选择下单": st.column_config.CheckboxColumn("勾选该款", default=False),
                    "🔥 还有几本待下单": st.column_config.NumberColumn("🔥 待下单本数", format="%d 本")
                },
                disabled=["🔥 还有几本待下单", "📖 预售书名", "⏰ 官方截单时间", "🚚 预计官方发货时间", "买家列表"],
                use_container_width=True,
                key="presale_summary_editor"
            )
            
            selected_pre_rows = edited_presale[edited_presale["选择下单"] == True]
            
            if not selected_pre_rows.empty:
                st.markdown(f"### 🎯 已勾选了 **{len(selected_pre_rows)}** 款不同的预售书准备统一下单")
                
                with st.form("presale_batch_form"):
                    pre_total_cost = st.number_input("这批勾选预售书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入供应商账单总价，系统会自动平摊到这几本书的每个单子上")
                    
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary"):
                        # 收集所有选中的原始订单 ID
                        all_target_ids = []
                        for _, row in selected_pre_rows.iterrows():
                            # 通过行索引反查原始 ids
                            matched_idx = row.name
                            orig_ids = presale_summary.loc[matched_idx, "原始订单ids"]
                            all_target_ids.extend(orig_ids)
                        
                        split_pc = pre_total_cost / len(all_target_ids) if len(all_target_ids) > 0 else 0.0
                        
                        for oid in all_target_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_pc
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功将选中的预售书籍批量更新为【我方已下单】！总成本已平摊（共涉及 {len(all_target_ids)} 个买家订单）。")
                        st.rerun()
            else:
                st.warning("👆 请在上方的表格中勾选你本次在供应商处下单的预售款式。")
        else:
            st.success("🎉 太棒了！当前没有任何等待下单的预售订单。")
    else:
        st.info("暂无数据。")

# ====== TAB 4: 自动发货与取件码汇总 (支持聚合显示买家所有不同的闲鱼单号) ======
if tab4:
    sub_col1, sub_col2 = st.columns([3, 1])
    with sub_col1:
        st.subheader("🚚 待发货包裹智能看板 (手机适配版)")
    with sub_col2:
        if st.button("🔄 刷新看板", key="refresh_shipping"):
            st.rerun()
            
    st.info("💡 手机端优化版：每个买家一个独立卡片！自动汇总并展示该买家所有的不同闲鱼单号。")
    
    if not df.empty:
        arrived_buyers = df[(df["status"] == "已到货") & (df["buyer_name"] != "暂无")]["buyer_name"].unique()
        shipping_df = df[df["buyer_name"].isin(arrived_buyers)].copy()
        
        if not shipping_df.empty:
            for col in ["buyer_address", "pickup_area", "book_image", "xianyu_no", "deadline"]:
                if col not in shipping_df.columns:
                    shipping_df[col] = ""
                else:
                    shipping_df[col] = shipping_df[col].fillna("")

            today_date = datetime.date.today()
            def calc_remaining_days(d_str):
                try:
                    if not d_str: return 999
                    return (pd.to_datetime(d_str).date() - today_date).days
                except:
                    return 999

            shipping_df["remaining_days"] = shipping_df["deadline"].apply(calc_remaining_days)
            group_cols = [c for c in ["buyer_name", "shop_name"] if c in shipping_df.columns]
            
            grouped = list(shipping_df.groupby(group_cols))
            
            def get_min_days(g_item):
                return g_item[1]["remaining_days"].min()
            
            grouped = sorted(grouped, key=get_min_days)
            
            for name_key, group in grouped:
                b_name = name_key[0]
                s_name = name_key[1] if len(name_key) > 1 else ""
                
                books = group["book_name"].tolist()
                statuses = group["status"].tolist()
                has_unarrived = any(st_val != "已到货" for st_val in statuses)
                
                min_days = group["remaining_days"].min()
                total_sell = group["price_sell"].sum()
                
                # 🎯 核心升级：提取并去重该买家名下的所有不同闲鱼单号
                all_xianyu_nos = [str(x).strip() for x in group["xianyu_no"].tolist() if x and str(x).strip() and str(x).strip() != "nan"]
                unique_xianyu_nos = sorted(list(set(all_xianyu_nos)))
                xianyu_display_str = " / ".join(unique_xianyu_nos) if unique_xianyu_nos else "无"
                
                current_address = group["buyer_address"].iloc[0] if group["buyer_address"].iloc[0] else ""
                current_pickup = group["pickup_area"].iloc[0] if group["pickup_area"].iloc[0] else ""
                
                if min_days == 999: days_str = "无限制"
                elif min_days < 0: days_str = f"🔴 已超期 {-min_days} 天"
                elif min_days == 0: days_str = "⚠️ 今天截止"
                elif min_days <= 5: days_str = f"🔥 仅剩 {min_days} 天"
                else: days_str = f"⏳ 剩 {min_days} 天"
                
                card_title = f"📦 买家: {b_name} | 店铺: {s_name} | 总额: ¥{total_sell:.2f} | 倒计时: {days_str}"
                if has_unarrived:
                    card_title = f"🔴【有未到货】{card_title}"
                
                with st.expander(card_title, expanded=False):
                    if has_unarrived:
                        un_list = [f"{b} [{st}]" for b, st in zip(books, statuses) if st != "已到货"]
                        st.warning(f"⚠️ 注意：该买家名下有其他未到货商品：{' / '.join(un_list)}")
                    
                    st.markdown("##### 📖 购买书单明细：")
                    for idx, b_item in enumerate(books):
                        st.markdown(f"- **书本 {idx+1}**：{b_item}")
                        
                    images = [str(i) for i in group["book_image"] if i and str(i).startswith("data:image")]
                    if images:
                        st.markdown("##### 📸 书本照片：")
                        cols_img = st.columns(min(len(images), 4))
                        for i, img_data in enumerate(images):
                            with cols_img[i % 4]:
                                st.image(img_data, width=100)
                                
                    st.write("---")
                    
                    with st.form(key=f"form_shipping_{b_name}_{s_name}"):
                        f_col1, f_col2 = st.columns(2)
                        with f_col1:
                            new_addr = st.text_area("📍 收货地址", value=current_address, height=80)
                        with f_col2:
                            new_pickup = st.text_input("🏷️ 取件码", value=current_pickup)
                            # 🎯 在这里完整展示该买家的所有闲鱼单号
                            st.markdown(f"🏷️ **关联闲鱼单号**：`{xianyu_display_str}`")
                            
                        act_col1, act_col2 = st.columns(2)
                        with act_col1:
                            save_btn = st.form_submit_button("💾 保存此买家地址/取件码", type="secondary")
                        with act_col2:
                            ship_btn = st.form_submit_button("🚀 一键标记该买家【已发货】", type="primary")
                            
                        if save_btn:
                            for _, t_row in group.iterrows():
                                supabase.table("orders").update({
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }).eq("id", int(t_row["id"])).execute()
                            st.success(f"✅ 买家【{b_name}】的收货地址与取件码已更新！")
                            st.rerun()
                            
                        if ship_btn:
                            for _, t_row in group.iterrows():
                                supabase.table("orders").update({
                                    "status": "已发货",
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }).eq("id", int(t_row["id"])).execute()
                            st.success(f"🚀 买家【{b_name}】的所有订单已成功标记为【已发货】并移出待发货区！")
                            import time
                            time.sleep(0.8)
                            st.rerun()
        else:
            st.info("📦 当前没有任何买家的包裹处于【已到货】状态。")
    else:
        st.info("暂无数据。")
        
# ====== TAB 5: 自由勾选包裹合拼与国际运费管理 (按时间升序排序) ======
if tab5:
    st.subheader("📦 自由勾选包裹合拼与国际运费管理")
    st.info("💡 操作指南：表格已按【下单时间】由远及近排序（越早下单的越靠前）。在左侧勾选你想要【合拼在一起】的任意书籍行，输入总运费后点击保存即可！")
    
    if not df.empty:
        # 筛选尚未最终发货的有效订单
        if "status" in df.columns:
            active_df = df[(df["buyer_name"] != "暂无") & (df["status"] != "已发货")].copy()
        else:
            active_df = df[df["buyer_name"] != "暂无"].copy()
            
        if not active_df.empty:
            if "shipping_fee" not in active_df.columns:
                active_df["shipping_fee"] = 0.0
            else:
                active_df["shipping_fee"] = active_df["shipping_fee"].fillna(0.0)
                
            # 🎯 核心排序逻辑：按订单时间升序排列（越早下单的越前面）
            if "order_time" in active_df.columns:
                active_df = active_df.sort_values(by="order_time", ascending=True)
                
            # 插入勾选列
            active_df.insert(0, "选择合拼", False)
            
            # 准备展示的精简列（含下单时间方便你核对批次）
            display_cols_map = {
                "选择合拼": "☑️ 勾选合拼",
                "id": "订单ID",
                "book_name": "📦 书名",
                "order_time": "下单时间",
                "status": "当前状态",
                "shipping_fee": "已绑定的国际运费"
            }
            
            for c in display_cols_map.keys():
                if c not in active_df.columns and c != "选择合拼":
                    active_df[c] = ""
                    
            table_view = active_df[list(display_cols_map.keys())].rename(columns=display_cols_map)
            
            # 交互式表格：允许自由勾选
            edited_table = st.data_editor(
                table_view,
                column_config={
                    "☑️ 勾选合拼": st.column_config.CheckboxColumn("☑️ 勾选合拼", default=False),
                    "已绑定的国际运费": st.column_config.NumberColumn("已绑定的国际运费 (¥)", format="¥%.2f"),
                },
                disabled=["订单ID", "📦 书名", "下单时间", "当前状态", "已绑定的国际运费"],
                use_container_width=True,
                key="free_consolidation_editor",
                hide_index=True
            )
            
            st.write("---")
            
            # 统一录入运费并合拼的表单
            with st.form(key="form_free_consolidate_action"):
                st.markdown("##### ✈️ 为当前勾选的包裹统一录入国际运费")
                col_f1, col_f2 = st.columns([2, 1])
                
                with col_f1:
                    batch_shipping_fee = st.number_input(
                        "填写这几本勾选项的总国际运费 (¥)", 
                        value=0.0, 
                        min_value=0.0, 
                        format="%.2f"
                    )
                with col_f2:
                    st.write("")
                    st.write("")
                    submit_merge = st.form_submit_button("📦 确认合拼并保存运费", type="primary")
                    
                if submit_merge:
                    selected_rows = edited_table[edited_table["☑️ 勾选合拼"] == True]
                    
                    if not selected_rows.empty:
                        selected_ids = selected_rows["订单ID"].tolist()
                        
                        for o_id in selected_ids:
                            supabase.table("orders").update({
                                "shipping_fee": batch_shipping_fee,
                                "status": "已合包裹"
                            }).eq("id", int(o_id)).execute()
                            
                        st.success(f"✅ 成功将选中的 {len(selected_ids)} 本书合为一个包裹！国际运费 ¥{batch_shipping_fee:.2f} 已保存，状态已更新为【已合包裹】。")
                        import time
                        time.sleep(0.8)
                        st.rerun()
                    else:
                        st.warning("⚠️ 请先在上方表格左侧勾选需要合拼的书籍行！")
        else:
            st.info("📦 当前没有可供合拼的待发货订单。")
    else:
        st.info("暂无数据。")


# ====== TAB 6: 月度营收统计 ======
if tab6:
    st.subheader("📊 月度营收与利润统计看板")
    st.info("💡 此页面按月份聚合展示你的订单营收、进货成本、国际运费以及预估净利润。")
    
    if not df.empty and "order_time" in df.columns:
        stats_df = df[df["buyer_name"] != "暂无"].copy()
        
        if not stats_df.empty:
            stats_df["month"] = pd.to_datetime(stats_df["order_time"], errors="coerce").dt.strftime("%Y-%m")
            stats_df["month"] = stats_df["month"].fillna("未知月份")
            
            for col in ["price_sell", "price_buy", "shipping_fee"]:
                if col not in stats_df.columns:
                    stats_df[col] = 0.0
                else:
                    stats_df[col] = stats_df[col].fillna(0.0)
                    
            monthly_summary = stats_df.groupby("month").agg(
                订单数=("id", "count"),
                总营收=("price_sell", "sum"),
                总进价=("price_buy", "sum"),
                总运费=("shipping_fee", "sum")
            ).reset_index()
            
            monthly_summary["总成本"] = monthly_summary["总进价"] + monthly_summary["总运费"]
            monthly_summary["净利润"] = monthly_summary["总营收"] - monthly_summary["总成本"]
            monthly_summary = monthly_summary.sort_values(by="month", ascending=False)
            
            tot_rev = monthly_summary["总营收"].sum()
            tot_cost = monthly_summary["总成本"].sum()
            tot_net = monthly_summary["净利润"].sum()
            
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.metric("💵 历史总营收", f"¥{tot_rev:.2f}")
            with m_col2:
                st.metric("🏷️ 历史总成本(进价+运费)", f"¥{tot_cost:.2f}")
            with m_col3:
                st.metric("📈 历史总净利润", f"¥{tot_net:.2f}", delta_color="normal" if tot_net >= 0 else "inverse")
                
            st.write("---")
            st.markdown("##### 📅 各月份详细账目清单：")
            
            display_monthly = monthly_summary.rename(columns={
                "month": "月份",
                "订单数": "订单书本数"
            })
            
            st.dataframe(
                display_monthly,
                column_config={
                    "总营收": st.column_config.NumberColumn("总营收 (¥)", format="¥%.2f"),
                    "总进价": st.column_config.NumberColumn("总进价 (¥)", format="¥%.2f"),
                    "总运费": st.column_config.NumberColumn("总运费 (¥)", format="¥%.2f"),
                    "总成本": st.column_config.NumberColumn("总成本 (¥)", format="¥%.2f"),
                    "净利润": st.column_config.NumberColumn("净利润 (¥)", format="¥%.2f"),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("📦 暂无统计数据。")
    else:
        st.info("暂无数据。")
