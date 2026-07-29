import hashlib
import streamlit as st

st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")

LOGO_URL = "https://yt3.googleusercontent.com/Vq_2Qi_2QV366SkPqhyvBSnx8QHpUiKXwVd9e_QoMH-mq_1y2CIT5Qx01pCXO4HfOfZCPItqzA=s160-c-k-c0x00ffffff-no-rj"
st.logo(LOGO_URL)


def verify_password(password, stored_value):
    """stored_value is 'salt$hash' as produced by generate_password_hash.py."""
    try:
        salt, digest = stored_value.split("$")
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return check == digest


def require_login():
    if st.session_state.get("authenticated"):
        return

    st.image(LOGO_URL, width=120)
    st.title("CHE Operational Dashboard")
    st.write("Log in to continue.")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        credentials = st.secrets.get("credentials", {})
        stored_value = credentials.get(username)
        if stored_value and verify_password(password, stored_value):
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            st.rerun()
        else:
            st.error("Incorrect username or password.")

    st.stop()


require_login()

with st.sidebar:
    st.caption(f"Signed in as {st.session_state['username']}")
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

home = st.Page("pages/home.py", title="Home", icon="🏠", default=True)
ticket_extractor = st.Page("pages/ticket_extractor.py", title="Ticket Extractor", icon="🎫")
compare_datasets = st.Page("pages/compare_datasets.py", title="Compare Datasets", icon="🔍")
itinerary_designer = st.Page("pages/itinerary_designer.py", title="Itinerary Designer", icon="🗺️")
trip_data_extractor = st.Page("pages/trip_data_extractor.py", title="Participant Data Extractor", icon="🧳")

# Add future tools to this list as you build them, e.g.:
# another_tool = st.Page("pages/another_tool.py", title="Another Tool", icon="🛠️")
# pg = st.navigation([home, ticket_extractor, compare_datasets, itinerary_designer, trip_data_extractor, another_tool])

pg = st.navigation([home, ticket_extractor, compare_datasets, itinerary_designer, trip_data_extractor])
pg.run()
