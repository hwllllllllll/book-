import streamlit as st
import pandas as pd
from datetime import datetime
import os

# 配置数据存储文件和选项
DATA_FILE = "orders.csv"
COLUMNS = ["买家账号", "下单时间", "书名", "下单店铺", "订单状态", "买家下单价", "我方成本价"]
SHOPS = ["一店", "二店"]
STATUSES = ["买家已下单", "我方已下单", "已到货", "准备发货", "已发货"]

# 页面基本设置
st.set_page_config(page_title="图书销售商家后台", layout="wide")
st.title("📚 图书销售商家后台管理系统")

# 如果本地没有数据文件，则自动创建一个空表格
if not os.path.exists(DATA_FILE):
    df_init = pd.DataFrame(columns=COLUMNS)
    df_init.to_csv(DATA_FILE, index=False)

# 读取最新数据
@st.cache_data(ttl=1)
def load_data():
    return pd.read_csv(DATA_FILE)

df = load_data()

# ---------------- 核心功能 1：录入新订单 ----------------
with st.expander("➕ 录入新订单", expanded=True):
    with st.form("new_order_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            buyer = st.text_input("1. 买家账号名字")
            book = st.text_input("2. 书名")
            price_sell = st.number_input("5. 买家下单价格", min_value=0.0, format="%.2f")
            
        with col2:
            shop = st.selectbox("3. 下单店铺", SHOPS)
            status = st.selectbox("4. 当前订单状态", STATUSES)
            price_buy = st.number_input("5. 我方下单价格 (成本)", min_value=0.0, format="%.2f")
            
        submit = st.form_submit_button("保存订单")
        
        if submit:
            if not buyer or not book:
                st.error("请填写买家账号和书名！")
            else:
                # 自动获取当前时间
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_record = pd.DataFrame([{
                    "买家账号": buyer,
                    "下单时间": current_time,
                    "书名": book,
                    "下单店铺": shop,
                    "订单状态": status,
                    "买家下单价": price_sell,
                    "我方成本价": price_buy
                }])
                # 追加到数据表中并保存为 CSV
                df = pd.concat([df, new_record], ignore_index=True)
                df.to_csv(DATA_FILE, index=False)
                st.success(f"成功添加订单：{book}")
                st.rerun()

# ---------------- 核心功能 2：管理与查找数据 ----------------
st.divider()
st.subheader("📋 订单管理看板 (支持直接修改状态)")

# 提供搜索功能，方便查找
search_query = st.text_input("🔍 搜索买家账号或书名：")
if search_query:
    df_display = df[df["买家账号"].str.contains(search_query, na=False) | df["书名"].str.contains(search_query, na=False)]
else:
    df_display = df.copy()

# 使用可编辑的数据表格展示
edited_df = st.data_editor(
    df_display,
    column_config={
        "下单店铺": st.column_config.SelectboxColumn("下单店铺", options=SHOPS, required=True),
        "订单状态": st.column_config.SelectboxColumn(
            "订单状态", 
            help="点击可切换订单进度", 
            options=STATUSES, 
            required=True
        ),
        "下单时间": st.column_config.DatetimeColumn("下单时间", disabled=True)
    },
    num_rows="dynamic",
    use_container_width=True,
    key="data_editor"
)

# 保存表格内的修改
if st.button("💾 确认更新修改内容", type="primary"):
    # 将编辑后的数据合并回原数据（如果是搜索状态下编辑的也能正确覆盖）
    if search_query:
        df.update(edited_df)
        df.to_csv(DATA_FILE, index=False)
    else:
        edited_df.to_csv(DATA_FILE, index=False)
    st.success("数据更新成功！")