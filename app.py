import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# —— 1. 认证并打开 Google Sheets ——
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(
    st.secrets["gcp_service_account"], scope
)
client = gspread.authorize(creds)

# 打开两个工作表：Expenses 和 Participants
book = client.open("Trip Expenses")
sheet = book.worksheet("Expenses")
participants_sheet = book.worksheet("Participants")

st.title("🚗 Trip Expense Tracker")

# —— 2. 管理参与者 —— 
st.subheader("🙋‍♂️ Manage Participants")
# 每次运行都重新读取最新的成员列表
names = participants_sheet.col_values(1)[1:]  # 跳过表头
with st.form("add_participant", clear_on_submit=True):
    new_name = st.text_input("New participant name")
    if st.form_submit_button("Add Participant"):
        nm = new_name.strip()
        if nm and nm not in names:
            participants_sheet.append_row([nm])
            st.success(f"✅ Added participant: {nm}. Please refresh to see the update.")
        else:
            st.error("❌ Name is empty or already exists.")

# —— 3. 新增费用记录 —— 
st.subheader("➕ Add Expense")
with st.form("add_expense", clear_on_submit=True):
    date = st.date_input("Date")
    desc = st.text_input("Description")
    amt  = st.number_input("Amount", min_value=0.0, format="%.2f")
    payer = st.selectbox("Payer", options=names)
    participants = st.multiselect(
        "Participants", options=names, default=names
    )
    if st.form_submit_button("Add Expense"):
        sheet.append_row([
            date.strftime("%Y-%m-%d"),
            desc,
            amt,
            payer,
            ", ".join(participants)
        ])
        st.success("✅ Expense added! Page will refresh to show it.")

# —— 4. 读取并展示所有记录 —— 
records = sheet.get_all_records()
df = pd.DataFrame(records)
st.subheader("📋 All Expenses")
st.dataframe(df)

# —— 6. 汇总与结算 —— 
if not df.empty:
    names = participants_sheet.col_values(1)[1:]
    # 已付金额
    paid = {n: df.loc[df["Payer"] == n, "Amount"].sum() for n in names}

    # 计算每笔的份额
    df["Share"] = df.apply(
        lambda r: r["Amount"] / len(r["Participants"].split(",")), axis=1
    )
    # 应付金额
    owed = {}
    for n in names:
        owed[n] = df[
            df["Participants"]
              .apply(lambda s: n in [x.strip() for x in s.split(",")])
        ]["Share"].sum()

    # 净额
    net = {n: paid[n] - owed[n] for n in names}

    # Summary 表
    st.subheader("💰 Summary")
    summary_df = pd.DataFrame({
        "Paid": paid,
        "Owed": owed,
        "Net":  net
    })
    st.table(summary_df)

    # Settlement 矩阵
    st.subheader("🔄 Settlement Matrix")
    settle_df = pd.DataFrame(0, index=names, columns=names)
    temp_net = net.copy()
    for payer in names:
        for payee in names:
            if temp_net[payer] < 0 and temp_net[payee] > 0:
                x = min(-temp_net[payer], temp_net[payee])
                settle_df.loc[payer, payee] = x
                temp_net[payer] += x
                temp_net[payee]  -= x
    st.table(settle_df)

# —— 5. 编辑已有记录 —— 
st.subheader("✏️ Edit an Expense")
if df.empty:
    st.info("No records to edit.")
else:
    # 在 DataFrame 中计算对应到 Google Sheet 的行号
    df = df.reset_index(drop=True)
    df["sheet_row"] = df.index + 2  # 表头是第1行，数据从第2行开始

    # 让用户选择想要编辑的行号
    row_to_edit = st.selectbox(
        "Select row to edit (sheet row number)",
        df["sheet_row"].tolist()
    )
    record = df[df["sheet_row"] == row_to_edit].iloc[0]

    with st.form("edit_expense", clear_on_submit=True):
        date_e = st.date_input("Date", value=pd.to_datetime(record["Date"]))
        desc_e = st.text_input("Description", value=record["Description"])
        amt_e  = st.number_input(
            "Amount", value=float(record["Amount"]), format="%.2f"
        )
        payer_e = st.selectbox(
            "Payer", options=names, index=names.index(record["Payer"])
        )
        parts_default = [x.strip() for x in record["Participants"].split(",")]
        participants_e = st.multiselect(
            "Participants", options=names, default=parts_default
        )
        if st.form_submit_button("Update Expense"):
            updated = [
                date_e.strftime("%Y-%m-%d"),
                desc_e,
                amt_e,
                payer_e,
                ", ".join(participants_e)
            ]
            # 一次性更新 A–E 列
            sheet.update(f"A{row_to_edit}:E{row_to_edit}", [updated])
            st.success("✅ Record updated! Please refresh to see changes.")
