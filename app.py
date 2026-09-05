import streamlit as st
import pandas as pd
from supabase import create_client, Client

# 初始化 Supabase 连接
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

SHOPS = ["大号", "小号"]
STATUSES = ["买家已下单", "我方已下单","已合包", "已到货", "准备发货", "已发货"]

st.set_page_config(page_title="图书销售云后台", layout="wide")
st.title("☁️ 图书销售商家后台 (Supabase 驱动)")

# 读取云端数据
@st.cache_data(ttl=2) # 2秒刷新一次缓存
def load_data():
    response = supabase.table("orders").select("*").execute()
    return pd.DataFrame(response.data)

df = load_data()

# ---------------- 录入新订单 ----------------
with st.expander("➕ 录入新订单", expanded=True):
    with st.form("new_order"):
        c1, c2 = st.columns(2)
        with c1:
            buyer = st.text_input("买家账号")
            book = st.text_input("书名")
            p_sell = st.number_input("买家下单价", min_value=0.0)
        with c2:
            shop = st.selectbox("店铺", SHOPS)
            status = st.selectbox("状态", STATUSES)
            p_buy = st.number_input("我方下单价", min_value=0.0)
        
        if st.form_submit_button("保存到云端"):
            if buyer and book:
                # 写入 Supabase
                supabase.table("orders").insert({
                    "buyer_name": buyer,
                    "book_name": book,
                    "shop_name": shop,
                    "status": status,
                    "price_sell": p_sell,
                    "price_buy": p_buy
                }).execute()
                st.success("订单已极速同步至云端！")
                st.rerun()
            else:
                st.error("请输入买家账号和书名。")

# ---------------- 订单看板 (状态修改) ----------------
st.divider()
st.subheader("📋 云端实时管理看板")

if not df.empty:
    # 按照下单时间倒序排列（新订单在最前面）
    df = df.sort_values(by="order_time", ascending=False)
    
    # 状态快速修改区
    st.markdown("##### 快速更新状态")
    edit_col1, edit_col2, edit_col3 = st.columns([2, 2, 1])
    with edit_col1:
        # 下拉选择要修改的订单 (用 ID 和 名字组合展示)
        order_list = df["id"].astype(str) + " - " + df["buyer_name"] + " (" + df["book_name"] + ")"
        selected_order = st.selectbox("选择要更新的订单", order_list)
    with edit_col2:
        new_status = st.selectbox("更改为新状态", STATUSES)
    with edit_col3:
        st.write("") 
        st.write("") # 占位对齐
        if st.button("🔄 更新状态"):
            # 提取选中的订单 ID
            target_id = int(selected_order.split(" - ")[0])
            # 更新云数据库
            supabase.table("orders").update({"status": new_status}).eq("id", target_id).execute()
            st.success("状态更新成功！")
            st.rerun()

    # 展示完整表格
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("目前云端数据库还没有订单哦，快去添加一笔吧！")