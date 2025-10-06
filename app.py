import os
import json
from datetime import date as _date

import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials


# ============ 1) Auth (Plan B: st.secrets -> fallback local JSON) ============
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

def load_sa_credentials():
    try:
        return st.secrets["gcp_service_account"]
    except Exception:
        pass
    json_path = os.path.join(os.path.dirname(__file__), "service_account.json")
    with open(json_path, "r") as f:
        return json.load(f)

sa_dict = load_sa_credentials()
creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_dict, scope)
client = gspread.authorize(creds)

# ============ 2) Open Sheets ============
book = client.open("Trip Expenses")          # change if needed
sheet = book.worksheet("Expenses")           # A:F = Date,Desc,Amount,Payer,Participants,Currency
participants_sheet = book.worksheet("Participants")

st.title("🚗 Trip Expense Tracker")

# ============ 3) Settings & constants ============
currency_options = ["USD", "EUR", "TWD"]

# session state defaults (tab-specific selectors)
if "tab_selected_currency" not in st.session_state:
    st.session_state.tab_selected_currency = currency_options[0]  # for "Selected" tab
if "fx_base" not in st.session_state:
    st.session_state.fx_base = currency_options[0]                # for "Converted" tab
if "fx_rates" not in st.session_state:
    st.session_state.fx_rates = {c: (1.0 if c == st.session_state.fx_base else None) for c in currency_options}

# ============ 4) Manage Participants ============
st.subheader("🙋‍♂️ Manage Participants")
names = participants_sheet.col_values(1)[1:]  # skip header
with st.form("add_participant", clear_on_submit=True):
    new_name = st.text_input("New participant name")
    if st.form_submit_button("Add Participant"):
        nm = new_name.strip()
        if nm and nm not in names:
            participants_sheet.append_row([nm])
            st.success(f"✅ Added participant: {nm}. Please refresh to see the update.")
        else:
            st.error("❌ Name is empty or already exists.")

# ============ 5) Add Expense (independent currency selector) ============
st.subheader("➕ Add Expense")
with st.form("add_expense", clear_on_submit=True):
    date_val = st.date_input("Date", value=_date.today())
    desc = st.text_input("Description")
    amt  = st.number_input("Amount", min_value=0.0, format="%.2f")
    payer = st.selectbox("Payer", options=names if names else [""])
    participants = st.multiselect("Participants", options=names, default=names)
    currency_in_form = st.selectbox("Currency", options=currency_options, index=0)  # default USD
    if st.form_submit_button("Add Expense"):
        if not names:
            st.error("❌ No participants yet. Please add participants first.")
        else:
            sheet.append_row([
                date_val.strftime("%Y-%m-%d"),
                desc,
                amt,
                payer,
                ", ".join(participants),
                currency_in_form
            ])
            st.success(f"✅ Expense added in {currency_in_form}! Page will refresh to show it.")

# ============ 6) Read all records & show one unified table ============
records = sheet.get_all_records()
df_all = pd.DataFrame(records)

# Backward-compat
if df_all.empty:
    df_all = pd.DataFrame(columns=["Date","Description","Amount","Payer","Participants","Currency"])
if "Currency" not in df_all.columns:
    df_all["Currency"] = currency_options[0]

# Normalize types
df_all["Amount"] = pd.to_numeric(df_all.get("Amount", 0.0), errors="coerce").fillna(0.0)

st.subheader("📋 All Expenses (All Currencies)")
st.dataframe(df_all)

# ============ 7) Settlement helpers ============
def _split_parts(cell):
    return [x.strip() for x in str(cell).split(",") if x and x.strip()]

def compute_net(df_like: pd.DataFrame, all_names: list[str]) -> dict:
    """Return net dict only (Paid - Owed)."""
    if df_like.empty or not all_names:
        return {n: 0.0 for n in all_names}

    # Paid
    paid = {n: float(df_like.loc[df_like["Payer"] == n, "Amount"].sum()) for n in all_names}

    # Share per row
    def _share(row):
        parts = _split_parts(row["Participants"])
        k = len(parts) or 1
        return float(row["Amount"]) / k

    df_like = df_like.copy()
    df_like["Share"] = df_like.apply(_share, axis=1)

    # Owed
    owed = {}
    for n in all_names:
        mask = df_like["Participants"].apply(lambda s: n in _split_parts(s))
        owed[n] = float(df_like.loc[mask, "Share"].sum())

    # Net
    net = {n: paid.get(n, 0.0) - owed.get(n, 0.0) for n in all_names}
    return net

def build_settlement_matrix(net: dict, all_names: list[str]) -> pd.DataFrame:
    """Greedy settlement from debtors to creditors; returns NxN matrix with amounts to pay."""
    settle_df = pd.DataFrame(0.0, index=all_names, columns=all_names)
    temp = net.copy()
    creditors = [n for n in all_names if temp[n] > 0]
    debtors   = [n for n in all_names if temp[n] < 0]
    ci, di = 0, 0
    while ci < len(creditors) and di < len(debtors):
        c = creditors[ci]
        d = debtors[di]
        give = min(temp[c], -temp[d])
        if give > 1e-9:
            settle_df.loc[d, c] = give
            temp[c] -= give
            temp[d] += give
        if temp[c] <= 1e-9: ci += 1
        if temp[d] >= -1e-9: di += 1
    return settle_df

def settlement_section_only(df_like: pd.DataFrame, all_names: list[str], title: str):
    st.markdown(f"**{title}**")
    if df_like.empty:
        st.info("No records.")
        return
    net = compute_net(df_like, all_names)
    settle_df = build_settlement_matrix(net, all_names)
    st.table(settle_df)

# ============ 8) Tabs: All Currencies -> Selected (with selector) -> Converted (FX settings inside) ============
if names:
    tabs = st.tabs([
        "All Currencies",
        "Selected",
        f"Converted (All → {st.session_state.fx_base})"
    ])

    # Tab 1: All Currencies (each currency's settlement only)
    with tabs[0]:
        unique_currencies = sorted([c for c in df_all["Currency"].dropna().unique().tolist()])
        if not unique_currencies:
            st.info("No currency data.")
        else:
            for c in unique_currencies:
                df_c = df_all[df_all["Currency"] == c].copy()
                with st.expander(f"{c} — Settlement Matrix", expanded=True):
                    settlement_section_only(df_c, names, f"{c}")

    # Tab 2: Selected (currency selector lives here)
    with tabs[1]:
        sel = st.selectbox("Pick a currency to view", options=currency_options,
                           index=currency_options.index(st.session_state.tab_selected_currency))
        st.session_state.tab_selected_currency = sel
        df_curr = df_all[df_all["Currency"] == sel].copy()
        settlement_section_only(df_curr, names, f"🔄 {sel} — Settlement Matrix")

        # Edit section (filtered by selected currency)
        st.subheader("✏️ Edit an Expense")
        df_all_reset = df_all.reset_index(drop=True)
        df_all_reset["sheet_row"] = df_all_reset.index + 2   # header = row 1
        editable = df_all_reset[df_all_reset["Currency"] == sel].copy()
        if editable.empty:
            st.info(f"No {sel} records to edit.")
        else:
            row_to_edit = st.selectbox("Select row to edit (sheet row number)",
                                       editable["sheet_row"].tolist())
            record = editable[editable["sheet_row"] == row_to_edit].iloc[0]
            with st.form("edit_expense", clear_on_submit=True):
                date_e = st.date_input("Date", value=pd.to_datetime(record["Date"]))
                desc_e = st.text_input("Description", value=record["Description"])
                amt_e  = st.number_input("Amount", value=float(record["Amount"]), format="%.2f")
                payer_e = st.selectbox("Payer", options=names,
                                       index=names.index(record["Payer"]) if record["Payer"] in names else 0)
                parts_default = _split_parts(record["Participants"])
                participants_e = st.multiselect("Participants", options=names, default=parts_default)
                currency_e = st.selectbox("Currency", options=currency_options,
                                          index=currency_options.index(record.get("Currency", sel)))
                if st.form_submit_button("Update Expense"):
                    updated = [
                        date_e.strftime("%Y-%m-%d"),
                        desc_e,
                        amt_e,
                        payer_e,
                        ", ".join(participants_e),
                        currency_e
                    ]
                    sheet.update(f"A{row_to_edit}:F{row_to_edit}", [updated])
                    st.success("✅ Record updated! Please refresh to see changes.")

    # Tab 3: Converted (FX settings inside this tab)
    with tabs[2]:
        st.subheader("💹 FX Settings (Manual)")
        fx_base = st.selectbox("Base currency", options=currency_options,
                               index=currency_options.index(st.session_state.fx_base))
        if fx_base != st.session_state.fx_base:
            st.session_state.fx_base = fx_base
            for c in currency_options:
                st.session_state.fx_rates[c] = 1.0 if c == fx_base else st.session_state.fx_rates.get(c, None)

        with st.form("fx_form_inside_tab"):
            st.write(f"Set rates as: 1 unit = X {st.session_state.fx_base}")
            rate_inputs = {}
            for c in currency_options:
                if c == st.session_state.fx_base:
                    st.number_input(f"{c} → {st.session_state.fx_base}", value=1.0, disabled=True, help="Base currency is always 1.0")
                    rate_inputs[c] = 1.0
                else:
                    default_val = st.session_state.fx_rates.get(c, 0.0) or 0.0
                    rate_inputs[c] = st.number_input(f"{c} → {st.session_state.fx_base}",
                                                     min_value=0.0, value=float(default_val),
                                                     step=0.0001, format="%.4f")
            if st.form_submit_button("Save FX"):
                st.session_state.fx_rates.update(rate_inputs)
                st.success("✅ FX rates saved.")

        fx_rates = st.session_state.fx_rates.copy()

        # Validate missing rates
        missing = [c for c in df_all["Currency"].unique().tolist() if fx_rates.get(c) in (None, 0)]
        if missing:
            st.warning(f"⚠️ Please set FX rate(s) for: {', '.join(missing)}. 1 {missing[0]} = ? {st.session_state.fx_base}")

        # Convert all expenses to base currency
        df_conv = df_all.copy()
        def _to_base(row):
            r = fx_rates.get(row["Currency"])
            return float(row["Amount"]) * float(r) if r not in (None, 0) else float("nan")
        df_conv["Amount"] = df_conv.apply(_to_base, axis=1)
        df_conv = df_conv.dropna(subset=["Amount"])

        st.caption(f"All expenses converted to **{st.session_state.fx_base}** using the manual FX rates above.")
        settlement_section_only(df_conv, names, f"🌐 Converted to {st.session_state.fx_base} — Settlement Matrix")

else:
    st.info("Please add participants to see settlements.")