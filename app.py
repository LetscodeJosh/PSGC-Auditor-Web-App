import io
import json
import time
from urllib.parse import quote
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="ERPN PSGC 1-by-1 Auditor",
    page_icon="✅",
    layout="wide",
)

# Theme-adaptive CSS (Dark Mode & Light Mode safe)
st.markdown(
    """
    <style>
        .block-container { padding-top: 1.2rem; padding-bottom: 1.5rem; }
        div[data-testid="stMetric"] {
            background-color: rgba(128, 128, 128, 0.12) !important;
            border: 1px solid rgba(128, 128, 128, 0.28) !important;
            padding: 14px 18px !important;
            border-radius: 10px !important;
        }
        div[data-testid="stMetricLabel"] p,
        div[data-testid="stMetricValue"] div {
            color: inherit !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

SYSTEM_KEYS = {
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "doctype",
    "is_group",
    "old_parent",
    "lft",
    "rgt",
    "parent_psgc_location",
    "parent",
    "parentfield",
    "parenttype",
    "psgc_code",
    "code",
    "_user_tags",
    "_comments",
    "_assign",
    "_liked_by",
}


@st.cache_resource(show_spinner=False)
def get_erpn_session(base_url: str, username: str, password: str):
    """Authenticates with ERPNext and returns a persistent session."""
    base_url = base_url.rstrip("/")
    session = requests.Session()
    resp = session.post(
        f"{base_url}/api/method/login",
        data={"usr": username, "pwd": password},
        timeout=15,
    )
    resp.raise_for_status()
    return session


def extract_doc_name(data: dict, code: str, fallback_names: dict | None = None) -> str:
    """Extracts the human-readable Location Name from an ERPNext PSGC Location doc."""
    code_str = str(code).strip()
    for k in (
        "location_name",
        "psgc_location_name",
        "name1",
        "location",
        "psgc_name",
        "place_name",
        "title",
        "description",
        "label",
    ):
        val = data.get(k)
        if isinstance(val, str) and val.strip() and val.strip() != code_str:
            return val.strip()

    for k, val in data.items():
        if k not in SYSTEM_KEYS and isinstance(val, str):
            v_clean = val.strip()
            if v_clean and v_clean != code_str and not v_clean.isdigit():
                return v_clean

    if fallback_names and code_str in fallback_names and fallback_names[code_str]:
        return fallback_names[code_str]
    return code_str


def detect_name_fieldname(sample_data: dict, sample_code: str) -> str | None:
    """Auto-detects the custom fieldname used for Location Name in ERPNext."""
    code_str = str(sample_code).strip()
    for k in (
        "location_name",
        "psgc_location_name",
        "name1",
        "location",
        "psgc_name",
        "place_name",
        "title",
        "description",
        "label",
    ):
        val = sample_data.get(k)
        if isinstance(val, str) and val.strip() and val.strip() != code_str:
            return k
    for k, val in sample_data.items():
        if k not in SYSTEM_KEYS and isinstance(val, str):
            v_clean = val.strip()
            if v_clean and v_clean != code_str and not v_clean.isdigit():
                return k
    return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_erpn_psgc_hierarchy(
    base_url: str, username: str, password: str
) -> tuple[dict, dict]:
    """Fetches Regions, Provinces, and Cities from live ERPNext PSGC Location doctype."""
    base_url = base_url.rstrip("/")
    session = get_erpn_session(base_url, username, password)

    # 1. Inspect a known root region doc (0100000000) to auto-detect the Location Name fieldname
    sample_resp = session.get(
        f"{base_url}/api/resource/PSGC Location/0100000000", timeout=15
    )
    sample_resp.raise_for_status()
    sample_doc = sample_resp.json().get("data", {})
    name_field = detect_name_fieldname(sample_doc, "0100000000")

    fields_list = ["name", "parent_psgc_location"]
    if name_field:
        fields_list.append(name_field)

    id_to_name = {}
    id_to_parent = {}

    # 2. Fetch Level 1 Regions (parent_psgc_location is not set)
    resp_reg = session.get(
        f"{base_url}/api/resource/PSGC Location",
        params={
            "fields": json.dumps(fields_list),
            "filters": json.dumps(
                [["PSGC Location", "parent_psgc_location", "is", "not set"]]
            ),
            "limit_page_length": 100,
        },
        timeout=20,
    )
    resp_reg.raise_for_status()
    regions = resp_reg.json().get("data", [])
    reg_ids = []
    for r in regions:
        cid = str(r.get("name", "")).strip()
        if cid:
            reg_ids.append(cid)
            id_to_parent[cid] = ""
            id_to_name[cid] = extract_doc_name(r, cid)

    if not reg_ids:
        return id_to_name, id_to_parent

    # 3. Fetch Level 2 Provinces (parent_psgc_location in reg_ids)
    resp_prov = session.get(
        f"{base_url}/api/resource/PSGC Location",
        params={
            "fields": json.dumps(fields_list),
            "filters": json.dumps(
                [["PSGC Location", "parent_psgc_location", "in", reg_ids]]
            ),
            "limit_page_length": 500,
        },
        timeout=20,
    )
    resp_prov.raise_for_status()
    provinces = resp_prov.json().get("data", [])
    prov_ids = []
    for p in provinces:
        cid = str(p.get("name", "")).strip()
        parent = str(p.get("parent_psgc_location") or "").strip()
        if cid:
            prov_ids.append(cid)
            id_to_parent[cid] = parent
            id_to_name[cid] = extract_doc_name(p, cid)

    # 4. Fetch Level 3 Cities/Municipalities in chunks
    chunk_size = 25
    for i in range(0, len(prov_ids), chunk_size):
        chunk = prov_ids[i : i + chunk_size]
        resp_city = session.get(
            f"{base_url}/api/resource/PSGC Location",
            params={
                "fields": json.dumps(fields_list),
                "filters": json.dumps(
                    [["PSGC Location", "parent_psgc_location", "in", chunk]]
                ),
                "limit_page_length": 1000,
            },
            timeout=25,
        )
        resp_city.raise_for_status()
        for c in resp_city.json().get("data", []):
            cid = str(c.get("name", "")).strip()
            parent = str(c.get("parent_psgc_location") or "").strip()
            if cid:
                id_to_parent[cid] = parent
                id_to_name[cid] = extract_doc_name(c, cid)

    return id_to_name, id_to_parent


def fetch_single_psgc_doc(
    session: requests.Session,
    base_url: str,
    code: str,
    fallback_names: dict | None = None,
) -> dict | None:
    """Fetches a single PSGC Location document directly from ERPNext by ID."""
    if not code:
        return None
    base_url = base_url.rstrip("/")
    code_str = str(code).strip()
    url = f"{base_url}/api/resource/PSGC Location/{quote(code_str)}"
    resp = session.get(url, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if not data:
        return None

    loc_name = extract_doc_name(data, code_str, fallback_names)
    parent = data.get("parent_psgc_location") or data.get("parent") or ""
    return {
        "name": str(data.get("name", code_str)).strip(),
        "location_name": loc_name,
        "parent_psgc_location": str(parent).strip() if parent else "",
    }


@st.cache_data(show_spinner=False)
def load_fallback_tree(file_bytes: bytes) -> tuple[dict, dict]:
    """Loads PSGC_Clean sheet from uploaded workbook if available."""
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    if "PSGC_Clean" not in xls.sheet_names:
        return {}, {}
    psgc_clean = pd.read_excel(
        io.BytesIO(file_bytes), sheet_name="PSGC_Clean", dtype=str
    ).fillna("")
    id_to_name = {}
    id_to_parent = {}
    for _, r in psgc_clean.iterrows():
        reg_id = str(r["Region_ID"]).strip()
        reg_name = str(r["Region_Name"]).strip()
        prov_id = str(r["Province_ID"]).strip()
        prov_name = str(r["Province_Name"]).strip()
        city_id = str(r["City_ID"]).strip()
        city_name = str(r["City_Name"]).strip()

        id_to_name[reg_id] = reg_name
        id_to_parent[reg_id] = ""
        id_to_name[prov_id] = prov_name
        id_to_parent[prov_id] = reg_id
        id_to_name[city_id] = city_name
        id_to_parent[city_id] = prov_id

    return id_to_name, id_to_parent


def get_combined_psgc_maps(
    base_url: str, username: str, password: str, file_bytes: bytes | None = None
) -> tuple[dict, dict, str]:
    """Combines Live ERPNext PSGC hierarchy with fallback sheet names."""
    fb_names, fb_parents = ({}, {})
    if file_bytes is not None:
        fb_names, fb_parents = load_fallback_tree(file_bytes)

    try:
        live_names, live_parents = fetch_erpn_psgc_hierarchy(
            base_url, username, password
        )
        if live_parents:
            merged_names = dict(fb_names)
            for k, v in live_names.items():
                if v and v != k:
                    merged_names[k] = v
                elif k not in merged_names:
                    merged_names[k] = v
            merged_parents = {**fb_parents, **live_parents}
            return merged_names, merged_parents, "Live ERPNext Doctype"
    except Exception:
        pass

    return fb_names, fb_parents, "Embedded PSGC Reference"


def audit_single_triplet(
    reg_code: str,
    prov_code: str,
    city_code: str,
    base_url: str,
    username: str,
    password: str,
    id_to_name: dict,
    id_to_parent: dict,
    progress_bar=None,
) -> dict:
    """Audits a single (Region, Province, City/Municipality) 1-by-1 with progress bar."""
    reg_code = str(reg_code).strip()
    prov_code = str(prov_code).strip()
    city_code = str(city_code).strip()

    live_mode = False
    session = None
    try:
        session = get_erpn_session(base_url, username, password)
        live_mode = True
    except Exception:
        live_mode = False

    def resolve_node(code: str) -> dict | None:
        if live_mode and session is not None:
            try:
                doc = fetch_single_psgc_doc(session, base_url, code, id_to_name)
                if doc is not None:
                    return doc
            except Exception:
                pass
        if code in id_to_parent:
            return {
                "name": code,
                "location_name": id_to_name.get(code, code),
                "parent_psgc_location": id_to_parent.get(code, ""),
            }
        return None

    # Step 1: Verify Region
    if progress_bar:
        progress_bar.progress(30, text=f"Step 1/3: Checking Region ({reg_code})...")
        time.sleep(0.05)
    reg_doc = resolve_node(reg_code)
    reg_ok = (
        reg_doc is not None
        and len(reg_code) == 10
        and reg_doc["parent_psgc_location"] == ""
    )
    reg_msg = (
        "Valid Root Region"
        if reg_ok
        else (
            "Not found in PSGC"
            if not reg_doc
            else f"Invalid parent ({reg_doc['parent_psgc_location']})"
        )
    )

    # Step 2: Verify Province
    if progress_bar:
        progress_bar.progress(65, text=f"Step 2/3: Checking Province ({prov_code})...")
        time.sleep(0.05)
    prov_doc = resolve_node(prov_code)
    prov_ok = (
        prov_doc is not None
        and len(prov_code) == 10
        and prov_doc["parent_psgc_location"] == reg_code
    )
    prov_msg = (
        f"Linked to Region {reg_code}"
        if prov_ok
        else (
            "Not found in PSGC"
            if not prov_doc
            else f"Parent is {prov_doc['parent_psgc_location']} (Expected {reg_code})"
        )
    )

    # Step 3: Verify City/Municipality
    if progress_bar:
        progress_bar.progress(
            100, text=f"Step 3/3: Checking City/Municipality ({city_code})..."
        )
        time.sleep(0.05)
    city_doc = resolve_node(city_code)
    city_ok = (
        city_doc is not None
        and len(city_code) in (10, 11)
        and city_doc["parent_psgc_location"] == prov_code
    )
    city_msg = (
        f"Linked to Province {prov_code}"
        if city_ok
        else (
            "Not found in PSGC"
            if not city_doc
            else f"Parent is {city_doc['parent_psgc_location']} (Expected {prov_code})"
        )
    )

    return {
        "all_ok": reg_ok and prov_ok and city_ok,
        "source": "Live ERPNext Doctype" if live_mode else "Embedded PSGC Reference",
        "reg_ok": reg_ok,
        "reg_name": reg_doc["location_name"] if reg_doc else "NOT FOUND",
        "reg_msg": reg_msg,
        "prov_ok": prov_ok,
        "prov_name": prov_doc["location_name"] if prov_doc else "NOT FOUND",
        "prov_msg": prov_msg,
        "city_ok": city_ok,
        "city_name": city_doc["location_name"] if city_doc else "NOT FOUND",
        "city_msg": city_msg,
    }


def to_clean_excel_bytes(df_export: pd.DataFrame) -> bytes:
    """Exports validated DataFrame to Excel with strict Text (@) formatting."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_export.to_excel(writer, sheet_name="Institution", index=False)
        ws = writer.sheets["Institution"]
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=2, max_col=4):
            for cell in row:
                cell.number_format = "@"
    return output.getvalue()
    

# --- HEADER ---
st.title("🏥 ERPNext PSGC 1-by-1 Auditor")
st.caption(
    "Step-by-Step Hierarchy Validator • Searchable by `Region` → `Province` → `City/Municipality`"
)

tab_step, tab_manual = st.tabs(
    ["🔍 1-by-1 File Auditor", "🌐 Live PSGC Doctype Search & Tester"]
)

with tab_step:
    uploaded_file = st.file_uploader(
        "Upload Institution Excel File (`.xlsx`)", type=["xlsx"]
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        with st.spinner("Syncing PSGC Location hierarchy from ERPNext..."):
            id_to_name, id_to_parent, tree_source = get_combined_psgc_maps(
                erpn_url, erpn_user, erpn_pass, file_bytes
            )

        df_in = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, dtype=str).fillna("")
        required_cols = ["Institution Name", "Region", "Province", "City/Municipality"]
        missing_cols = [c for c in required_cols if c not in df_in.columns]

        if missing_cols:
            st.error(f"Missing required columns: {', '.join(missing_cols)}")
            st.stop()

        # Enrich dataframe with resolved ERPNext Region, Province, and City names for searching
        df_in["_row_num"] = range(1, len(df_in) + 1)
        df_in["_reg_name"] = (
            df_in["Region"].str.strip().map(lambda x: id_to_name.get(x, x))
        )
        df_in["_prov_name"] = (
            df_in["Province"].str.strip().map(lambda x: id_to_name.get(x, x))
        )
        df_in["_city_name"] = (
            df_in["City/Municipality"].str.strip().map(lambda x: id_to_name.get(x, x))
        )

        # Optional DOH source columns if Audit_Verification exists
        xls_sheets = pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names
        audit_source_df = None
        if "Audit_Verification" in xls_sheets:
            audit_source_df = pd.read_excel(
                io.BytesIO(file_bytes), sheet_name="Audit_Verification", dtype=str
            ).fillna("")

        total_rows = len(df_in)

        if (
            "current_idx" not in st.session_state
            or st.session_state.get("file_name") != uploaded_file.name
        ):
            st.session_state.current_idx = 0
            st.session_state.verified_rows = set()
            st.session_state.file_name = uploaded_file.name

        # --- SEARCHABLE PSGC FILTERS (Region -> Province -> City/Municipality) ---
        with st.container(border=True):
            st.caption(
                "🔎 **Search & Filter Records by ERPNext PSGC Region, Province, or City/Municipality**"
            )
            f_col1, f_col2, f_col3 = st.columns(3)

            reg_options_map = {}
            for r_code in df_in["Region"].str.strip().unique():
                if r_code:
                    reg_options_map[f"{id_to_name.get(r_code, r_code)} ({r_code})"] = (
                        r_code
                    )
            reg_labels = sorted(reg_options_map.keys())

            with f_col1:
                sel_reg_label = st.selectbox(
                    "Filter by Region",
                    options=reg_labels,
                    index=None,
                    placeholder="🔍 Search Region (Name or Code)...",
                )
                sel_reg_code = (
                    reg_options_map.get(sel_reg_label) if sel_reg_label else None
                )

            df_prov_scope = (
                df_in[df_in["Region"].str.strip() == sel_reg_code]
                if sel_reg_code
                else df_in
            )
            prov_options_map = {}
            for p_code in df_prov_scope["Province"].str.strip().unique():
                if p_code:
                    prov_options_map[f"{id_to_name.get(p_code, p_code)} ({p_code})"] = (
                        p_code
                    )
            prov_labels = sorted(prov_options_map.keys())

            with f_col2:
                sel_prov_label = st.selectbox(
                    "Filter by Province",
                    options=prov_labels,
                    index=None,
                    placeholder="🔍 Search Province (Name or Code)...",
                )
                sel_prov_code = (
                    prov_options_map.get(sel_prov_label) if sel_prov_label else None
                )

            df_city_scope = (
                df_prov_scope[df_prov_scope["Province"].str.strip() == sel_prov_code]
                if sel_prov_code
                else df_prov_scope
            )
            city_options_map = {}
            for c_code in df_city_scope["City/Municipality"].str.strip().unique():
                if c_code:
                    city_options_map[f"{id_to_name.get(c_code, c_code)} ({c_code})"] = (
                        c_code
                    )
            city_labels = sorted(city_options_map.keys())

            with f_col3:
                sel_city_label = st.selectbox(
                    "Filter by City/Municipality",
                    options=city_labels,
                    index=None,
                    placeholder="🔍 Search City/Municipality (Name or Code)...",
                )
                sel_city_code = (
                    city_options_map.get(sel_city_label) if sel_city_label else None
                )

        # Apply active Region / Province / City filters
        filtered_df = df_in.copy()
        if sel_reg_code:
            filtered_df = filtered_df[filtered_df["Region"].str.strip() == sel_reg_code]
        if sel_prov_code:
            filtered_df = filtered_df[
                filtered_df["Province"].str.strip() == sel_prov_code
            ]
        if sel_city_code:
            filtered_df = filtered_df[
                filtered_df["City/Municipality"].str.strip() == sel_city_code
            ]

        filtered_indices = filtered_df.index.tolist()
        if not filtered_indices:
            st.warning(
                "No institutions match the selected Region / Province / City filter."
            )
            st.stop()

        if st.session_state.current_idx not in filtered_indices:
            st.session_state.current_idx = filtered_indices[0]

        idx = st.session_state.current_idx
        pos_in_filter = filtered_indices.index(idx)
        verified_count = len(st.session_state.verified_rows)

        # Top KPI Metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "Current Record",
            f"{pos_in_filter + 1:,} / {len(filtered_indices):,}",
            help=f"Global Row {idx + 1} of {total_rows:,}",
        )
        m2.metric("Manually Verified", f"{verified_count:,} / {total_rows:,}")
        m3.metric("Remaining", f"{total_rows - verified_count:,}")
        m4.metric("Review Progress", f"{(verified_count / total_rows * 100):.1f}%")

        # --- 1-BY-1 NAVIGATION & OMNI-SEARCH BAR (Institution + Region + Province + City) ---
        nav1, nav2, nav3, nav4 = st.columns([1.2, 5.2, 1.2, 1.8])

        with nav1:
            if st.button(
                "⬅️ Previous",
                use_container_width=True,
                disabled=(pos_in_filter <= 0),
            ):
                st.session_state.current_idx = filtered_indices[pos_in_filter - 1]
                st.rerun()

        with nav2:
            option_labels = []
            for i in filtered_indices:
                r_item = df_in.iloc[i]
                check_mark = "✅ " if i in st.session_state.verified_rows else ""
                label = (
                    f"[Row {i + 1}] {check_mark}{r_item['Institution Name']} • "
                    f"{r_item['_reg_name']} | {r_item['_prov_name']} | "
                    f"{r_item['_city_name']} ({r_item['City/Municipality']})"
                )
                option_labels.append(label)

            selected_label = st.selectbox(
                "Search by Institution, Region, Province, City, or PSGC Code",
                options=option_labels,
                index=pos_in_filter,
                label_visibility="collapsed",
                placeholder="🔍 Type Institution, Region, Province, City, or PSGC Code...",
            )
            new_pos = option_labels.index(selected_label)
            new_idx = filtered_indices[new_pos]
            if new_idx != idx:
                st.session_state.current_idx = new_idx
                st.rerun()

        with nav3:
            if st.button(
                "Next ➡️",
                use_container_width=True,
                disabled=(pos_in_filter >= len(filtered_indices) - 1),
            ):
                st.session_state.current_idx = filtered_indices[pos_in_filter + 1]
                st.rerun()

        with nav4:
            if st.button("✅ Verify & Next", type="primary", use_container_width=True):
                st.session_state.verified_rows.add(idx)
                if pos_in_filter < len(filtered_indices) - 1:
                    st.session_state.current_idx = filtered_indices[pos_in_filter + 1]
                st.rerun()

        # Run 1-by-1 Audit on Current Record with Progress Bar
        cur_row = df_in.iloc[st.session_state.current_idx]
        inst_name = str(cur_row["Institution Name"]).strip()
        reg_code = str(cur_row["Region"]).strip()
        prov_code = str(cur_row["Province"]).strip()
        city_code = str(cur_row["City/Municipality"]).strip()

        pbar = st.progress(0, text="Initializing 1-by-1 PSGC hierarchy check...")
        res = audit_single_triplet(
            reg_code,
            prov_code,
            city_code,
            erpn_url,
            erpn_user,
            erpn_pass,
            id_to_name,
            id_to_parent,
            progress_bar=pbar,
        )
        pbar.progress(
            100,
            text=(
                f"1-by-1 Check Complete • Source: {res['source']} • "
                f"Row {st.session_state.current_idx + 1} of {total_rows}"
            ),
        )

        # Display 1-by-1 Audit Card
        with st.container(border=True):
            head_left, head_right = st.columns([3, 1])
            with head_left:
                st.subheader(f"🏥 {inst_name}")
            with head_right:
                if res["all_ok"]:
                    st.success("✅ 100% PSGC MATCH")
                else:
                    st.error("❌ HIERARCHY MISMATCH")

            c_reg, c_prov, c_city = st.columns(3)

            doh_reg, doh_prov, doh_city = "", "", ""
            if audit_source_df is not None and st.session_state.current_idx < len(
                audit_source_df
            ):
                arow = audit_source_df.iloc[st.session_state.current_idx]
                doh_reg = str(arow.get("DOH_Region", ""))
                doh_prov = str(arow.get("DOH_Province", ""))
                doh_city = str(arow.get("DOH_City", ""))

            with c_reg:
                with st.container(border=True):
                    st.caption("LEVEL 1 • REGION")
                    st.markdown(f"### `{reg_code}`")
                    st.write(f"**ERPNext Name:** {res['reg_name']}")
                    if doh_reg:
                        st.caption(f"DOH Original: {doh_reg}")
                    if res["reg_ok"]:
                        st.success(f"✔ {res['reg_msg']}")
                    else:
                        st.error(f"✖ {res['reg_msg']}")

            with c_prov:
                with st.container(border=True):
                    st.caption("LEVEL 2 • PROVINCE")
                    st.markdown(f"### `{prov_code}`")
                    st.write(f"**ERPNext Name:** {res['prov_name']}")
                    if doh_prov:
                        st.caption(f"DOH Original: {doh_prov}")
                    if res["prov_ok"]:
                        st.success(f"✔ {res['prov_msg']}")
                    else:
                        st.error(f"✖ {res['prov_msg']}")

            with c_city:
                with st.container(border=True):
                    st.caption("LEVEL 3 • CITY / MUNICIPALITY")
                    st.markdown(f"### `{city_code}`")
                    st.write(f"**ERPNext Name:** {res['city_name']}")
                    if doh_city:
                        st.caption(f"DOH Original: {doh_city}")
                    if res["city_ok"]:
                        st.success(f"✔ {res['city_msg']}")
                    else:
                        st.error(f"✖ {res['city_msg']}")

        # Download Clean Import File
        st.divider()
        dl_col1, dl_col2 = st.columns([3, 1])
        with dl_col1:
            st.caption(
                "Ready to import into ERPNext? Download the 4-column Institution file with Text-formatted PSGC codes."
            )
        with dl_col2:
            clean_export_df = df_in[
                ["Institution Name", "Region", "Province", "City/Municipality"]
            ]
            st.download_button(
                label="📥 Download ERPN Import (.xlsx)",
                data=to_clean_excel_bytes(clean_export_df),
                file_name="ERPN_Verified_Institution_Import.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

with tab_manual:
    st.subheader("🌐 Search & Test ERPNext PSGC Doctype (By Region, Province, and City)")
    with st.spinner("Loading ERPNext PSGC Location tree..."):
        m_names, m_parents, m_source = get_combined_psgc_maps(
            erpn_url,
            erpn_user,
            erpn_pass,
            uploaded_file.getvalue() if uploaded_file is not None else None,
        )

    if m_parents:
        all_regions = {
            f"{m_names.get(k, k)} ({k})": k
            for k, p in m_parents.items()
            if p == ""
        }
        reg_set = set(all_regions.values())
        all_provinces = {
            f"{m_names.get(k, k)} ({k})": k
            for k, p in m_parents.items()
            if p in reg_set
        }
        prov_set = set(all_provinces.values())
        all_cities = {
            f"{m_names.get(k, k)} ({k})": k
            for k, p in m_parents.items()
            if p in prov_set
        }

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            m_reg_label = st.selectbox(
                "1. Search Region",
                options=sorted(all_regions.keys()),
                index=None,
                placeholder="🔍 Type Region name or PSGC code...",
            )
            m_reg_code = all_regions.get(m_reg_label, "") if m_reg_label else ""

        with col_b:
            scoped_provs = {
                lbl: k
                for lbl, k in all_provinces.items()
                if not m_reg_code or m_parents.get(k) == m_reg_code
            }
            m_prov_label = st.selectbox(
                "2. Search Province",
                options=sorted(scoped_provs.keys()),
                index=None,
                placeholder="🔍 Type Province name or PSGC code...",
            )
            m_prov_code = scoped_provs.get(m_prov_label, "") if m_prov_label else ""

        with col_c:
            scoped_cities = {
                lbl: k
                for lbl, k in all_cities.items()
                if (not m_prov_code or m_parents.get(k) == m_prov_code)
                and (
                    not m_reg_code
                    or m_parents.get(m_parents.get(k, ""), "") == m_reg_code
                )
            }
            m_city_label = st.selectbox(
                "3. Search City/Municipality",
                options=sorted(scoped_cities.keys()),
                index=None,
                placeholder="🔍 Type City/Municipality name or PSGC code...",
            )
            m_city_code = scoped_cities.get(m_city_label, "") if m_city_label else ""

        effective_city = m_city_code
        effective_prov = m_prov_code or (
            m_parents.get(effective_city, "") if effective_city else ""
        )
        effective_reg = m_reg_code or (
            m_parents.get(effective_prov, "") if effective_prov else ""
        )

        if effective_reg and effective_prov and effective_city:
            m_pbar = st.progress(0, text="Checking selected PSGC hierarchy...")
            m_res = audit_single_triplet(
                effective_reg,
                effective_prov,
                effective_city,
                erpn_url,
                erpn_user,
                erpn_pass,
                m_names,
                m_parents,
                progress_bar=m_pbar,
            )
            if m_res["all_ok"]:
                st.success(
                    f"✅ **100% Valid Hierarchy** ({m_res['source']}): "
                    f"**Region:** `{effective_reg}` ({m_res['reg_name']}) → "
                    f"**Province:** `{effective_prov}` ({m_res['prov_name']}) → "
                    f"**City/Municipality:** `{effective_city}` ({m_res['city_name']})"
                )
            else:
                st.error(
                    f"❌ **Hierarchy Mismatch**: Region: {m_res['reg_msg']} | "
                    f"Province: {m_res['prov_msg']} | City: {m_res['city_msg']}"
                )
        else:
            st.info(
                "👆 Search and select a **Region**, **Province**, or **City/Municipality** above to verify its exact ERPNext PSGC code hierarchy."
            )
