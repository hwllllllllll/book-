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
menu_options = [
    "📝 常规录入", 
    "📋 现货等待下单",  # 👈 修改这里
    "🔮 预售管理", 
    "🚚 发货看板", 
    "📦 包裹合拼与运费", 
    "📊 月度营收统计"
]

selected_tab = st.selectbox("📌 请选择功能页面", menu_options, label_visibility="collapsed")
st.write("---")

tab1 = (selected_tab == "📝 常规录入")
tab2 = (selected_tab == "📋 现货等待下单") 
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


# ==================== 📸 顶部：闲鱼截图智能识别 (图像预处理增强版) ====================
    with st.container():
        st.markdown("##### 📸 闲鱼截图智能识别 (AI 图像增强 + OCR 自动提取)")
        uploaded_screenshot = st.file_uploader("上传闲鱼订单截图", type=["jpg", "jpeg", "png"], key="auto_screenshot_input")
        
        if uploaded_screenshot is not None:
            st.image(uploaded_screenshot, width=200, caption="已上传待识别截图")
            if st.button("✨ 开始图像增强与智能识别", type="primary", key="parse_img_btn"):
                try:
                    import pytesseract
                    from PIL import Image, ImageEnhance, ImageFilter
                    import re

                    # 1. 读取原图
                    orig_image = Image.open(uploaded_screenshot)
                    
                    # ==================== 💡 核心：图像预处理（大幅提升 OCR 识别率） ====================
                    # ① 转为灰度图
                    gray_img = orig_image.convert('L')
                    
                    # ② 放大 2 倍（双三次插值，让文字边缘更清晰）
                    w, h = gray_img.size
                    resized_img = gray_img.resize((w * 2, h * 2), Image.Resampling.BICUBIC)
                    
                    # ③ 增强对比度
                    enhancer = ImageEnhance.Contrast(resized_img)
                    contrast_img = enhancer.enhance(2.0) # 提高对比度
                    
                    # ④ 锐化处理
                    sharpened_img = contrast_img.filter(ImageFilter.SHARPEN)
                    
                    # ⑤ 二值化（黑白化处理，过滤背景阴影和浅色干扰）
                    # 阈值设为 160，高于此的变白，低于此的变黑
                    threshold = 160
                    processed_img = sharpened_img.point(lambda p: 255 if p > threshold else 0)
                    # ======================================================================

                    # 运行 OCR 识别（使用处理后的高质量黑白大图）
                    extracted_text = pytesseract.image_to_string(processed_img, lang='chi_sim+eng')
                    
                    # 💡 展开查看增强后的 OCR 实际识别文本
                    with st.expander("🔍 点击查看增强 OCR 原始识别文本 (Debug)"):
                        st.text(extracted_text)

                    # 2. 强力提取买家昵称（兼容各种前后缀及换行）
                    detected_buyer = ""
                    buyer_match = re.search(r'买家昵称\s*[:：]?\s*([^\n\r]+)', extracted_text)
                    if buyer_match:
                        detected_buyer = buyer_match.group(1).strip()
                    else:
                        lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                        for i, line in enumerate(lines):
                            if "买家" in line or "昵称" in line:
                                cleaned_line = line.replace("买家昵称", "").replace("昵称", "").replace("买家", "").strip()
                                cleaned_line = re.sub(r'^[:：\s]+', '', cleaned_line)
                                if cleaned_line:
                                    detected_buyer = cleaned_line
                                    break
                                elif i + 1 < len(lines):
                                    detected_buyer = lines[i + 1]
                                    break

                    # 3. 智能提取书名（支持中英文、过滤杂质）
                    detected_book = ""
                    lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                    for line in lines:
                        if not any(kw in line for kw in ['买家', '订单编号', '付款时间', '下单时间', '商品总价', '运费', '成交价', '交易快照', '支付宝', '地址', '去发货', '编号']):
                            if len(line) >= 2 and any(c.isalnum() or ('\u4e00' <= c <= '\u9fff') for c in line):
                                clean_line = re.sub(r'【.*?】', '', line).strip()
                                if clean_line and len(clean_line) > 2 and '¥' not in clean_line and not clean_line.isdigit():
                                    base_detected = clean_line.split("特装")[0].split("普装")[0].split("明信片")[0].strip()
                                    if len(base_detected) >= 2:
                                        detected_book = base_detected
                                        break
                    
                    # 常用英文书名兜底
                    if not detected_book:
                        for line in lines:
                            if any(k in line.lower() for k in ["flashlight", "moral", "1+2", "青春报告"]):
                                clean_line = re.sub(r'【.*?】', '', line).strip()
                                detected_book = clean_line.split("特装")[0].split("普装")[0].strip()
                                break

                    # 4. 提取下单时间
                    time_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{2}:\d{2}:\d{2})', extracted_text)
                    detected_datetime_str = time_match.group(1).strip() if time_match else ""
                    
                    # 5. 提取价格
                    prices = re.findall(r'[¥￥]\s*(\d+\.\d{2})', extracted_text)
                    detected_price = float(prices[0]) if prices else 0.0
                    
                    # 6. 强力提取闲鱼单号（全局精准过滤所有 15-22 位数字）
                    all_long_numbers = re.findall(r'\d{15,25}', extracted_text)
                    detected_xianyu = ""
                    if all_long_numbers:
                        valid_orders = [num for num in all_long_numbers if 15 <= len(num) <= 22]
                        if valid_orders:
                            detected_xianyu = valid_orders[0]
                        else:
                            detected_xianyu = all_long_numbers[0]

                    # 💡 写入状态自动回填到表单变量中
                    if detected_buyer:
                        st.session_state["t1_buyer"] = detected_buyer
                    if detected_price > 0:
                        st.session_state["t1_price_editable"] = detected_price
                    if detected_xianyu:
                        st.session_state["t1_xianyu"] = detected_xianyu
                    if detected_book:
                        st.session_state["t1_manual_book"] = detected_book
                        st.session_state["t1_history_book"] = "-- 手动输入新书名 / 或从下方选择 --"
                        
                    if detected_datetime_str:
                        try:
                            dt_obj = pd.to_datetime(detected_datetime_str)
                            st.session_state["t1_date"] = dt_obj.date()
                            st.session_state["t1_time"] = dt_obj.time()
                        except:
                            pass
                        
                    st.success(f"🎉 增强识别成功！\n- 买家昵称: {detected_buyer or '未识别'}\n- 书名: {detected_book or '未识别'}\n- 价格: ¥{detected_price}\n- 单号: {detected_xianyu or '未识别'}\n- 时间: {detected_datetime_str or '未识别'}")
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
            
            
# ====== TAB 2: 现货等待下单 ======
if tab2:
    st.markdown("### ⏳ 现货等待下单区")
    st.info("💡 显示所有属性为【现货】且状态为【买家已下单】的订单。在此多选并填写总成本后一键变更为【我方已下单】。")
    
    if not df.empty:
        # 🎯 筛选：现货 + 买家已下单
        spot_wait_df = df[(df["stock_type"] == "现货") & (df["status"] == "买家已下单")].copy()
        
        if not spot_wait_df.empty:
            # 插入勾选列
            spot_wait_df.insert(0, "勾选下单", False)
            
            # 🔄 重新排序列：把书名 (book_name) 紧跟在勾选框后面，提到最前面！
            cols_order = [
                "勾选下单", 
                "book_name",     # 👈 书名排在第 2 位
                "id", 
                "buyer_name", 
                "price_sell", 
                "deadline", 
                "order_time"
            ]
            
            # 过滤出当前表里真实存在的列
            available_cols = [c for c in cols_order if c in spot_wait_df.columns]
            
            # 友好化表头名称
            display_df = spot_wait_df[available_cols].rename(columns={
                "book_name": "📖 书名",
                "id": "订单编号",
                "buyer_name": "买家账号",
                "price_sell": "买家下单价",
                "deadline": "发货截止日期",
                "order_time": "下单时间"
            })
            
            edited_spot = st.data_editor(
                display_df,
                column_config={
                    "勾选下单": st.column_config.CheckboxColumn("勾选打包", default=False)
                },
                disabled=["📖 书名", "订单编号", "买家账号", "买家下单价", "发货截止日期", "下单时间"],
                use_container_width=True,
                key="spot_wait_editor",
                hide_index=True  # 👈 隐藏最左侧自带的 0,1,2,3 行号，节省手机屏幕空间
            )
            
            selected_rows = edited_spot[edited_spot["勾选下单"] == True]
            
        if not selected_rows.empty:
                st.markdown(f"#### 🛒 已勾选 **{len(selected_rows)}** 个现货订单")
                
                with st.form("spot_batch_form"):
                    spot_total_cost = st.number_input("这批勾选现货的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入供应商账单总价，系统会自动平摊到这些书的成本中")
                    
                    if st.form_submit_button("⚡ 确认现货已下单并平摊成本", type="primary"):
                        target_ids = selected_rows["订单编号"].tolist()
                        split_cost = spot_total_cost / len(target_ids) if len(target_ids) > 0 else 0.0
                        
                        # 自动生成当前时间的包裹采购批次号
                        import datetime
                        package_batch = f"PKG-{datetime.datetime.now().strftime('%m%d-%H%M%S')}"
                        
                        for oid in target_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_cost,
                                "package_id": package_batch  # 把这批书绑定在这个包裹号里
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功将勾选的现货订单变更为【我方已下单】！已生成包裹批次号：{package_batch}")
                        import time
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.success("🎉 目前没有需要去下单的现货订单！")
    else:
        st.info("暂无数据。")


     # ====== TAB 3: 预售专区 (预售订单全流程跟踪) ======
if tab3:
    st.markdown("### 🔮 预售全流程管理")
    
    # ==================== 第一阶段：待下单 ====================
    st.subheader("🛒 第一阶段：待下单汇总")
    st.info("💡 如果因为限购等原因只能部分下单，可下拉修改【本次下单数量】，系统会按买家下单时间的【先来后到】优先分配！")
    
    if not df.empty:
        # 按 order_time 排序，保证优先分配给早下单的买家
        if "order_time" in df.columns:
            presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].sort_values(by="order_time").copy()
        else:
            presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].copy()
            
        if not presale_wait_df.empty:
            group_cols = ["book_name"]
            
            presale_summary = presale_wait_df.groupby(group_cols).agg(
                待下单数量=("id", "count"),
                official_cutoff_time=("official_cutoff_time", "min"), 
                official_shipping_time=("official_shipping_time", "min"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            presale_summary = presale_summary.sort_values(by="待下单数量", ascending=False)
            
            presale_summary = presale_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 最早截单时间",
                "official_shipping_time": "🚚 最早发货时间",
                "待下单数量": "🔥 待下单总数"
            })
            
            presale_summary.insert(0, "选择下单", False)
            presale_summary.insert(1, "本次下单数量", presale_summary["🔥 待下单总数"])
            
            # 🎯 核心优化：动态生成下拉菜单的最大选项数量
            max_possible_qty = int(presale_summary["🔥 待下单总数"].max())
            # 生成 [1, 2, 3, ... max] 的列表作为下拉选项
            qty_options = list(range(1, max_possible_qty + 1))
            
            cols_order = ["选择下单", "本次下单数量", "🔥 待下单总数", "📖 预售书名", "⏰ 最早截单时间", "🚚 最早发货时间", "买家列表"]
            available_pre_cols = [c for c in cols_order if c in presale_summary.columns]
            
            edited_presale = st.data_editor(
                presale_summary[available_pre_cols],
                column_config={
                    "选择下单": st.column_config.CheckboxColumn("☑️ 确认下单", default=False),
                    # 🎯 核心修改：替换为下拉选择列
                    "本次下单数量": st.column_config.SelectboxColumn(
                        "🛒 下单数量 (下拉)", 
                        options=qty_options,
                        help="如遇到限购，请点击下拉修改本次实际买到的数量"
                    ),
                    "🔥 待下单总数": st.column_config.NumberColumn("🔥 待下单总数", disabled=True)
                },
                disabled=["🔥 待下单总数", "📖 预售书名", "⏰ 最早截单时间", "🚚 最早发货时间", "买家列表"],
                use_container_width=True,
                key="presale_summary_editor"
            )
            
            selected_pre_rows = edited_presale[edited_presale["选择下单"] == True]
            
            if not selected_pre_rows.empty:
                st.markdown(f"#### 🎯 已勾选了 **{len(selected_pre_rows)}** 款预售书准备下单")
                
                with st.form("presale_batch_form"):
                    pre_total_cost = st.number_input("这批勾选预售书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入总价，系统会自动平摊")
                    
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary"):
                        all_target_ids = []
                        
                        for _, row in selected_pre_rows.iterrows():
                            matched_idx = row.name
                            orig_ids = presale_summary.loc[matched_idx, "原始订单ids"]
                            
                            request_qty = int(row["本次下单数量"])
                            max_qty = len(orig_ids)
                            # 绝对安全的底层兜底：就算下拉不小心选多了，也按实际存在的上限扣
                            actual_qty = min(request_qty, max_qty) 
                            
                            target_ids = orig_ids[:actual_qty]
                            all_target_ids.extend(target_ids)
                        
                        split_pc = pre_total_cost / len(all_target_ids) if len(all_target_ids) > 0 else 0.0
                        
                        import datetime
                        package_batch = f"PKG-{datetime.datetime.now().strftime('%m%d-%H%M%S')}"
                        
                        for oid in all_target_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_pc,
                                "package_id": package_batch
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功完成采购！本次实际下单 **{len(all_target_ids)}** 本（未买够的会自动保留在上方）。批次号：{package_batch}")
                        import time
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.success("🎉 太棒了！当前没有任何等待下单的预售订单。")
            
        st.write("---")
      # ==================== 第二阶段：等待发货 ====================
        st.subheader("⏳ 第二阶段：等待官方发货汇总")
        st.info("📦 这里显示的是你【已经向官方下单】但还没发货的预售款。时刻盯紧发货日期！")
        
        # 🎯 筛选预售且状态为“我方已下单”的需求
        presale_shipping_df = df[(df["stock_type"] == "预售") & (df["status"] == "我方已下单")].copy()
        
        if not presale_shipping_df.empty:
            group_cols = ["book_name", "official_cutoff_time", "official_shipping_time"]
            shipping_summary = presale_shipping_df.groupby(group_cols).agg(
                等待发货数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            # 按发货时间排序，越早发货的排在越前面
            shipping_summary = shipping_summary.sort_values(by="official_shipping_time", ascending=True)
            
            shipping_summary = shipping_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 官方截单时间",
                "official_shipping_time": "🚚 预计官方发货时间",
                "等待发货数量": "⏳ 苦等发货本数"
            })
            
            shipping_summary.insert(0, "标记已发货", False)
            cols_order_ship = ["标记已发货", "⏳ 苦等发货本数", "📖 预售书名", "🚚 预计官方发货时间", "⏰ 官方截单时间", "买家列表"]
            available_ship_cols = [c for c in cols_order_ship if c in shipping_summary.columns]
            
            edited_shipping = st.data_editor(
                shipping_summary[available_ship_cols],
                column_config={
                    "标记已发货": st.column_config.CheckboxColumn("勾选已发货", default=False),
                    "⏳ 苦等发货本数": st.column_config.NumberColumn("⏳ 待发本数", format="%d 本")
                },
                disabled=["⏳ 苦等发货本数", "📖 预售书名", "⏰ 官方截单时间", "🚚 预计官方发货时间", "买家列表"],
                use_container_width=True,
                key="shipping_summary_editor"
            )
            
            selected_shipping_rows = edited_shipping[edited_shipping["标记已发货"] == True]
            
            if not selected_shipping_rows.empty:
                st.markdown(f"#### 📦 已勾选 **{len(selected_shipping_rows)}** 款，官方终于发货啦！")
                
                if st.button("🚀 批量标记为【官方已发货】", type="primary", key="btn_confirm_shipping"):
                    all_ship_ids = []
                    for _, row in selected_shipping_rows.iterrows():
                        matched_idx = row.name
                        orig_ids = shipping_summary.loc[matched_idx, "原始订单ids"]
                        all_ship_ids.extend(orig_ids)
                        
                    for oid in all_ship_ids:
                        supabase.table("orders").update({
                            "status": "官方已发货" 
                        }).eq("id", int(oid)).execute()
                        
                    st.success(f"✅ 成功将勾选的预售书标记为【官方已发货】状态（共涉及 {len(all_ship_ids)} 个单子）！")
                    import time
                    time.sleep(0.5)
                    st.rerun()
        else:
            st.success("🎉 目前没有卡在等待官方发货阶段的预售书！")

# ====== TAB 4: 自动发货与取件码汇总 (支持聚合显示买家所有不同的闲鱼单号) ======
if tab4:
    sub_col1, sub_col2 = st.columns([3, 1])
    with sub_col1:
        st.subheader("🚚 待发货包裹智能看板 (手机适配版)")
    with sub_col2:
        if st.button("🔄 刷新看板", key="refresh_shipping"):
            st.rerun()
            
    st.info("💡 手机端优化版：自动屏蔽未到货商品！仅显示【官方已发货/已到达我方仓库/已合包/已到货】的书籍。如果买家还有书没到，系统会智能提醒！")
    
    if not df.empty:
        # 🎯 核心升级 1：严格过滤，只有这四种状态的书才有资格进入发货看板！
        allowed_statuses = ["官方已发货", "已到达我方仓库", "已合包", "已到货"]
        shipping_df = df[(df["status"].isin(allowed_statuses)) & (df["buyer_name"] != "暂无")].copy()
        
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
                
                # 🎯 核心升级 2：全局去数据库查一下，这个买家是不是还有别的东西没到？
                buyer_all_orders = df[df["buyer_name"] == b_name]
                unarrived_orders = buyer_all_orders[~buyer_all_orders["status"].isin(allowed_statuses + ["卖家已发货", "已完结"])]
                has_unarrived = not unarrived_orders.empty
                
                min_days = group["remaining_days"].min()
                total_sell = group["price_sell"].sum()
                
                # 提取并去重该买家名下的所有不同闲鱼单号
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
                
                # 如果他还有没到货的书，标题直接变红警告
                if has_unarrived:
                    card_title = f"🔴【还有未到货】{card_title}"
                
                with st.expander(card_title, expanded=False):
                    if has_unarrived:
                        # 🎯 提取未到货的书名给卖家提个醒，防呆设计！
                        un_list = [f"{row['book_name']} [{row['status']}]" for _, row in unarrived_orders.iterrows()]
                        st.warning(f"⚠️ 强烈建议等齐再发！该买家还有以下商品未到货（已自动从下方发货列表隐藏）：\n\n{' / '.join(un_list)}")
                    
                    st.markdown("##### 📖 本次可发货书单明细：")
                    for idx, (b_item, st_val) in enumerate(zip(books, statuses)):
                        st.markdown(f"- **书本 {idx+1}**：{b_item} `({st_val})`")
                        
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
                            st.markdown(f"🏷️ **关联闲鱼单号**：`{xianyu_display_str}`")
                            
                        act_col1, act_col2 = st.columns(2)
                        with act_col1:
                            save_btn = st.form_submit_button("💾 保存此买家地址/取件码", type="secondary")
                        with act_col2:
                            # 统一变更为【卖家已发货】
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
                                    "status": "卖家已发货", 
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }).eq("id", int(t_row["id"])).execute()
                            st.success(f"🚀 买家【{b_name}】的可发货订单已成功发出！")
                            import time
                            time.sleep(0.8)
                            st.rerun()
        else:
            st.info("📦 当前没有任何买家的包裹处于【官方已发货/已到达我方仓库/已合包】状态。")
    else:
        st.info("暂无数据。")
        
# ====== TAB 5: 官方包裹合拼与海外邮费结算 ======
if tab5:
    st.markdown("### 📦 官方包裹合拼与海外邮费分摊")
    st.info("💡 这里显示的是你【统一下单】生成的采购包裹。当包裹发往你这边并产生海外运费时，录入总邮费，系统会自动平摊到该包裹内的每一本书。")
    
    if not df.empty and "package_id" in df.columns:
        # 🎯 筛选出有包裹号、且需要结算邮费的订单（通常是我方已下单或官方已发货状态）
        # 同时过滤掉那些 package_id 为空的数据
        pack_df = df[df["package_id"].notna() & (df["package_id"] != "")].copy()
        
        if not pack_df.empty:
            # 获取所有唯一的包裹批次号
            package_list = pack_df["package_id"].unique().tolist()
            
            selected_pkg = st.selectbox("📦 第一步：选择到达的【采购包裹批次】", ["-- 请选择包裹批次 --"] + package_list)
            
            if selected_pkg != "-- 请选择包裹批次 --":
                # 取出这个包裹里的所有书
                pkg_orders = pack_df[pack_df["package_id"] == selected_pkg]
                
                st.markdown(f"#### 🛍️ 包裹 **{selected_pkg}** 内共有 **{len(pkg_orders)}** 本书：")
                
                display_df = pkg_orders[["id", "buyer_name", "book_name", "status", "price_buy"]].rename(
                    columns={
                        "id": "订单号", 
                        "buyer_name": "所属买家", 
                        "book_name": "📖 书名", 
                        "status": "当前状态",
                        "price_buy": "单本采购价"
                    }
                )
                st.dataframe(display_df, hide_index=True, use_container_width=True)
                
                with st.form("freight_calc_form"):
                    total_freight = st.number_input("🚢 第二步：填写该包裹的【海外总邮费】(¥)", min_value=0.0, format="%.2f")
                    
                    if st.form_submit_button("⚡ 确认平摊海外邮费", type="primary"):
                        # 计算单本分摊邮费
                        split_freight = total_freight / len(pkg_orders) if len(pkg_orders) > 0 else 0.0
                        order_ids = pkg_orders["id"].tolist()
                        
                        for oid in order_ids:
                            supabase.table("orders").update({
                                "shipping_fee": split_freight,  # 👈 记录每本书分摊的海外邮费
                                "status": "已到达我方仓库"        # 👈 可选：更新状态为已到货，你可以改成你习惯的状态名
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 包裹 {selected_pkg} 的海外邮费已成功分摊！每本书均摊邮费: ¥{split_freight:.2f}")
                        import time
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.success("🎉 目前没有待处理海外运费的包裹！")
    else:
        st.error("⚠️ 数据库中暂未检测到 `package_id` 字段，请确保已在 Supabase 中添加，并在下单区生成包裹。")


# ====== TAB 6: 📊 财务与月度营收统计 (双币种智能结算版) ======
if tab6:
    st.markdown("### 📊 财务与月度营收统计")
    st.info("💡 系统已为你开启【跨境双币核算】模式：买家收入为 RMB(¥)，采购与邮费支出为 HKD($)。设置下方汇率，系统会自动为你算出真实的净利润！")
    
    if not df.empty:
        # ================== 💱 汇率设置区 ==================
        st.markdown("#### 💱 当前汇率设置")
        # 默认汇率设为 0.92（你可以随时在页面上改成当天的实际汇率）
        current_rate = st.number_input("港币 (HKD) 兑换 人民币 (RMB) 汇率", value=0.9200, format="%.4f", help="例如填 0.92，代表 1 港币 = 0.92 人民币")
        st.write("---")
        
        # 筛选出已经“已发货”或“已完结”的订单来计算真实收益（或者你也可以算全部，这里默认算所有非空的单子）
        # 如果你想只算完结的，可以加条件： calc_df = df[df["status"].isin(["已完结", "卖家已发货"])]
        calc_df = df.copy()
        
        # ================== 💰 核心财务数据计算 ==================
        # 1. 总收入 (纯 RMB)
        total_income_rmb = calc_df["price_sell"].sum()
        
        # 2. 总支出 (纯 HKD = 书本采购 + 海外运费)
        total_book_cost_hkd = calc_df["price_buy"].sum()
        total_shipping_cost_hkd = calc_df["shipping_fee"].sum()
        total_expense_hkd = total_book_cost_hkd + total_shipping_cost_hkd
        
        # 3. 折算与利润 (转回 RMB)
        converted_expense_rmb = total_expense_hkd * current_rate
        net_profit_rmb = total_income_rmb - converted_expense_rmb
        
        # ================== 📈 数据大屏展示 ==================
        st.markdown("#### 📈 总体营收看板")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric(label="💰 买家总付款 (RMB)", value=f"¥ {total_income_rmb:,.2f}")
        with col2:
            st.metric(label="🛒 采购及运费总支出 (HKD)", value=f"HK$ {total_expense_hkd:,.2f}", 
                      delta=f"折合 RMB: -¥{converted_expense_rmb:,.2f}", delta_color="inverse")
        with col3:
            st.metric(label="🏆 实际净利润 (RMB)", value=f"¥ {net_profit_rmb:,.2f}")
            
        st.write("---")
        
        # ================== 📦 各包裹批次成本明细 ==================
        st.markdown("#### 📦 采购包裹 (批次) 利润核对明细")
        st.caption("这里展示每个采购包裹（HKD结算）具体赚了多少钱，方便你排查哪一批利润最高/亏钱了。")
        
        # 只筛选出有包裹批次号的数据
        pkg_df = calc_df[calc_df["package_id"].notna() & (calc_df["package_id"] != "")].copy()
        
        if not pkg_df.empty:
            # 按包裹批次进行分组核算
            pkg_summary = pkg_df.groupby("package_id").agg(
                包含书本数=("id", "count"),
                批次总收入_RMB=("price_sell", "sum"),
                批次采购支出_HKD=("price_buy", "sum"),
                批次邮费支出_HKD=("shipping_fee", "sum")
            ).reset_index()
            
            # 计算每个批次的总支出(HKD) 和 最终利润(RMB)
            pkg_summary["总支出_HKD"] = pkg_summary["批次采购支出_HKD"] + pkg_summary["批次邮费支出_HKD"]
            pkg_summary["折合支出_RMB"] = pkg_summary["总支出_HKD"] * current_rate
            pkg_summary["批次净利润_RMB"] = pkg_summary["批次总收入_RMB"] - pkg_summary["折合支出_RMB"]
            
            # 格式化一下名字让表格更好看
            pkg_display = pkg_summary.rename(columns={
                "package_id": "包裹批次号",
                "包含书本数": "书本量",
                "批次总收入_RMB": "总收入 (¥)",
                "总支出_HKD": "总成本 (HK$)",
                "批次净利润_RMB": "净利润 (¥)"
            })
            
            # 丢进前端展示
            st.dataframe(
                pkg_display[["包裹批次号", "书本量", "总收入 (¥)", "总成本 (HK$)", "净利润 (¥)"]].style.format({
                    "总收入 (¥)": "{:.2f}",
                    "总成本 (HK$)": "{:.2f}",
                    "净利润 (¥)": "{:.2f}"
                }), 
                hide_index=True, 
                use_container_width=True
            )
        else:
            st.info("尚无带有包裹批次号的订单以供分析。")
            
    else:
        st.info("系统暂无任何订单数据。")

# ==================== 🛠️ 全局订单数据修改区 ====================
st.markdown("---")
st.markdown("### 🛠️ 历史订单快速修改区 (Excel模式)")
st.info("💡 在下方的表格中，你可以直接双击【我方采购成本】、【运费】或【状态】进行修改，修改完成后点击保存即可同步到数据库。")

if not df.empty:
    # 筛选你最常需要修改的列展示出来
    edit_cols = ["id", "buyer_name", "book_name", "status", "price_sell", "price_buy", "shipping_fee", "package_id"]
    available_edit_cols = [c for c in edit_cols if c in df.columns]
    
    # 将原始数据转换为展示用的数据框
    edit_df = df[available_edit_cols].copy()
    
    # 按照 ID 倒序排列，最新的在最前面
    edit_df = edit_df.sort_values(by="id", ascending=False)
    
    # 显示可编辑表格
    edited_data = st.data_editor(
        edit_df,
        column_config={
            "id": st.column_config.NumberColumn("订单编号", disabled=True),
            "buyer_name": st.column_config.TextColumn("买家", disabled=True),
            "book_name": st.column_config.TextColumn("书名", disabled=True),
            "price_sell": st.column_config.NumberColumn("买家付款 (¥)", disabled=True),
            "status": st.column_config.SelectboxColumn("当前状态", options=["买家已下单", "我方已下单", "官方已发货", "已到达我方仓库", "卖家已发货", "已完结"]),
            "price_buy": st.column_config.NumberColumn("✏️ 我方采购成本 (¥)", min_value=0.0, format="%.2f"),
            "shipping_fee": st.column_config.NumberColumn("✏️ 均摊海外运费 (¥)", min_value=0.0, format="%.2f"),
            "package_id": st.column_config.TextColumn("✏️ 采购包裹批次号")
        },
        use_container_width=True,
        hide_index=True,
        key="global_data_editor"
    )
    
    # 检查是否有数据被修改
    if st.button("💾 确认保存上述表格的所有修改", type="primary"):
        # 找出修改过的行
        # 对比原 df 和 edited_data
        changed_count = 0
        for index, row in edited_data.iterrows():
            orig_row = edit_df.loc[index]
            
            # 如果这三列有任何一个发生了变化
            if (row["price_buy"] != orig_row["price_buy"]) or \
               (row["shipping_fee"] != orig_row["shipping_fee"]) or \
               (row["status"] != orig_row["status"]) or \
               (row["package_id"] != orig_row["package_id"]):
                
                # 同步到 Supabase 数据库
                supabase.table("orders").update({
                    "price_buy": float(row["price_buy"]) if pd.notna(row["price_buy"]) else 0.0,
                    "shipping_fee": float(row["shipping_fee"]) if pd.notna(row["shipping_fee"]) else 0.0,
                    "status": str(row["status"]),
                    "package_id": str(row["package_id"]) if pd.notna(row["package_id"]) else ""
                }).eq("id", int(row["id"])).execute()
                
                changed_count += 1
                
        if changed_count > 0:
            st.success(f"✅ 成功更新了 {changed_count} 笔订单的数据！")
            import time
            time.sleep(0.5)
            st.rerun()
        else:
            st.warning("⚠️ 没有检测到任何修改，无需保存。")
