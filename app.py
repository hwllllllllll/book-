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
st.title("☁️ 图书销售商家后台")

# 读取云端数据
@st.cache_data(ttl=2) 
def load_data():
    response = supabase.table("orders").select("*").execute()
    return pd.DataFrame(response.data)

df = load_data()

# ---------------- 使用选项卡分离功能 ----------------
tab1, tab2 = st.tabs(["📝 常规单笔录入", "📦 批量拼单录入 (系统自动算成本)"])

# ====== TAB 1: 常规单笔录入 (适合偶尔的一单) ======
with tab1:
    with st.form("new_order"):
        purchase_type = st.radio("📦 进货方式", ["独立下单", "合并拼单"], index=1, horizontal=True)
        st.write("") 
        
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
            p_sell = st.number_input("7. 买家下单总价(营收)", min_value=0.0)
            p_buy = st.number_input("8. 我方总成本价(成本)", min_value=0.0)
        
        if st.form_submit_button("💾 保存单笔订单"):
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
                    "purchase_type": purchase_type 
                }).execute()
                st.success("订单已同步至云端！")
                st.rerun()
            else:
                st.error("请输入买家账号和书名。")

# ====== TAB 2: 批量拼单录入 (自动平摊成本神器) ======
with tab2:
    st.info("💡 **使用说明**：当你在供应商处支付了一笔总花费（包含多本书）时，在下方填入总价并列出书籍。系统会自动帮你平摊成本，并拆分为独立的订单录入数据库。")
    
    # 只需要输入一次总成本
    batch_total_cost = st.number_input("💰 这批拼单的 **我方总花费 (成本)**", min_value=0.0, format="%.2f", help="输入这笔闲鱼订单你实际付了多少钱")
    
    st.write("👇 请在下方像填 Excel 一样录入这批书（可以点击表格最下方的加号新增更多行，多余的空行系统会自动忽略）：")
    
    # 创建一个默认带有 3 行的模板表格，买家账号如果暂时没有可以填"暂无"或"现货"
    init_data = [{"买家账号": "", "书名": "", "买家下单价(营收)": 0.0, "下单店铺": "一店", "闲鱼单号(选填)": "", "备注": "合并拼单"} for _ in range(3)]
    batch_df = pd.DataFrame(init_data)
    
    # data_editor 允许直接在网页上动态填表
    edited_batch = st.data_editor(
        batch_df, 
        num_rows="dynamic", 
        column_config={
            "下单店铺": st.column_config.SelectboxColumn("下单店铺", options=SHOPS),
            "买家下单价(营收)": st.column_config.NumberColumn("买家下单价(营收)", min_value=0.0, format="%.2f")
        },
        use_container_width=True
    )
    
    if st.button("⚡ 一键拆分并保存所有订单", type="primary"):
        # 自动过滤掉没有填“书名”或“买家”的空行
        valid_rows = edited_batch[(edited_batch["书名"].str.strip() != "") & (edited_batch["买家账号"].str.strip() != "")]
        
        if len(valid_rows) == 0:
            st.error("❌ 请至少填写一行有效的买家账号和书名！(如果没有买家可先填'暂无')")
        elif batch_total_cost <= 0:
            st.error("❌ 请填写这批拼单的总花费！")
        else:
            # 核心魔法：系统自动帮你做除法均摊
            per_item_cost = batch_total_cost / len(valid_rows)
            
            # 循环拆分，一条条存进云端
            for index, row in valid_rows.iterrows():
                supabase.table("orders").insert({
                    "buyer_name": row["买家账号"],
                    "xianyu_no": row["闲鱼单号(选填)"],
                    "book_name": row["书名"],
                    "shop_name": row["下单店铺"],
                    "status": "我方已下单",  # 既然是拼单进货，默认设为已下单
                    "price_sell": row["买家下单价(营收)"],
                    "price_buy": per_item_cost,
                    "remark": row["备注"],
                    "purchase_type": "合并拼单"
                }).execute()
                
            st.success(f"✅ 成功录入了 {len(valid_rows)} 笔拼单！系统已自动为它们各自记上了 {per_item_cost:.2f} 元的成本。")
            st.rerun()

# ---------------- 核心功能 3：订单看板 (状态修改) ----------------
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

    display_df = df.rename(columns={
        "id": "编号",
        "purchase_type": "进货方式",
        "buyer_name": "买家账号",
        "xianyu_no": "闲鱼单号",
        "book_name": "书名",
        "shop_name": "店铺",
        "status": "状态",
        "price_sell": "订单营收",
        "price_buy": "订单成本",
        "remark": "备注",
        "order_time": "下单时间"
    })
    
    cols_to_show = ["编号", "进货方式", "买家账号", "闲鱼单号", "书名", "店铺", "状态", "订单营收", "订单成本", "备注", "下单时间"]
    available_cols = [col for col in cols_to_show if col in display_df.columns]
    
    st.dataframe(display_df[available_cols], use_container_width=True, hide_index=True)
else:
    st.info("目前云端数据库还没有订单哦，快去添加一笔吧！")
