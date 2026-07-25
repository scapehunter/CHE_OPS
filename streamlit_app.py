import streamlit as st

st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")

LOGO_URL = "https://yt3.googleusercontent.com/Vq_2Qi_2QV366SkPqhyvBSnx8QHpUiKXwVd9e_QoMH-mq_1y2CIT5Qx01pCXO4HfOfZCPItqzA=s160-c-k-c0x00ffffff-no-rj"
st.logo(LOGO_URL)

home = st.Page("pages/home.py", title="Home", icon="🏠", default=True)
ticket_extractor = st.Page("pages/ticket_extractor.py", title="Ticket Extractor", icon="🎫")
compare_datasets = st.Page("pages/compare_datasets.py", title="Compare Datasets/Sheets et al", icon="🔍")

# Add future tools to this list as you build them, e.g.:
# another_tool = st.Page("pages/another_tool.py", title="Another Tool", icon="🛠️")
# pg = st.navigation([home, ticket_extractor, compare_datasets, another_tool])

pg = st.navigation([home, ticket_extractor, compare_datasets])
pg.run()