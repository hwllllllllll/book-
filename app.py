import streamlit as st
import pandas as pd
from supabase import create_client, Client

# 初始化 Supabase 连接
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

SHOPS = ["一店", "二店"]
STATUSES = ["买家已下单", "我方已下单", "已到货", "准备发货", "已发货"]

st.set_page_config(page_title="图书销售云后台", layout="wide")
st.title("☁️ 图书销售商家后台 (Supabase 驱动)")

# 读取云端数据
@st.cache_data(ttl=2) 
def load_data():
    response = supabase.table("orders").select("*").execute()
    return pd.DataFrame(response.data)

df = load_data()

# ---------------- 核心功能 1：录入新订单 ----------------
with st.expander("➕ 录入新订单", expanded=True):
    with st.form("new_order"):
        # 在顶部加入你需要的单选按钮
        purchase_type = st.radio("📦 进货方式", ["独立下单", "合并拼单"], horizontal=True)
        st.write("") # 稍微空一行更美观
        
        c1, c2, c3 = st.columns(3)
        with c1:
            buyer = st.text_input("1. 买家账号")
            xianyu = st.text_input("2. 闲鱼单号 (选填)")
            book = st.text_input("3. 书名")
        with c2:
            shop = st.selectbox("4. 下单店铺", SHOPS)
            status = st.selectbox("5. 当前订单状态", STATUSES)
            remark = st.text_input("6. 备注")
        with c3:
            p_sell = st.number_input("7. 买家下单总价", min_value=0.0)
            p_buy = st.number_input("8. 我方总成本价 (拼单请填均摊后价格)", min_value=0.0)
        
        if st.form_submit_button("保存订单到云端"):
            if buyer and book:
                supabase.table("orders").insert({
                    "buyer_name": buyer,
                    "xianyu_no": xianyu, 
                    "book_name": book,
                    "shop_name": shop,
                    "status": status,
                    "price_sell": p_sell,
                    "price_buy": p_buy,
                    "remark": remark,
                    "purchase_type": purchase_type # 保存进货方式
                }).execute()
                st.success("订单已极速同步至云端！")
                st.rerun()
            else:
                st.error("请输入买家账号和书名。")

# ---------------- 核心功能 2：订单看板 (状态修改) ----------------
st.divider()
st.subheader("📋 云端实时管理看板")

if not df.empty:
    df = df.sort_values(by="order_time", ascending=False)
    
    st.markdown("##### 快速更新状态")
    edit_col1, edit_col2, edit_col3 = st.columns([2, 2, 1])
    with edit_col1:
        order_list = df["id"].astype(str) + " - " + df["buyer_name"] + " (" + df["book_name"] + ")"
        selected_order = st.selectbox("选择要更新的订单", order_list)
    with edit_col2:
        new_status = st.selectbox("更改为新状态", STATUSES)
    with edit_col3:
        st.write("") 
        st.write("") 
        if st.button("🔄 更新状态"):
            target_id = int(selected_order.split(" - ")[0])
            supabase.table("orders").update({"status": new_status}).eq("id", target_id).execute()
            st.success("状态更新成功！")
            st.rerun()

    # 翻译为中文
    display_df = df.rename(columns={
        "id": "编号",
        "purchase_type": "进货方式",
        "buyer_name": "买家账号",
        "xianyu_no": "闲鱼单号",
        "book_name": "书名",
        "shop_name": "店铺",
        "status": "状态",
        "price_sell": "营收",
        "price_buy": "成本",
        "remark": "备注",
        "order_time": "下单时间"
    })
    
    # 调整表格的显示顺序
    cols_to_show = ["编号", "进货方式", "买家账号", "闲鱼单号", "书名", "店铺", "状态", "营收", "成本", "备注", "下单时间"]
    available_cols = [col for col in cols_to_show if col in display_df.columns]
    
    st.dataframe(display_df[available_cols], use_container_width=True, hide_index=True)
else:
    st.info("目前云端数据库还没有订单哦，快去添加一笔吧！")

    # 展示完整表格
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("目前云端数据库还没有订单哦，快去添加一笔吧！")
