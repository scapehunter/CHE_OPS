import streamlit as st

LOGO_URL = "https://yt3.googleusercontent.com/Vq_2Qi_2QV366SkPqhyvBSnx8QHpUiKXwVd9e_QoMH-mq_1y2CIT5Qx01pCXO4HfOfZCPItqzA=s160-c-k-c0x00ffffff-no-rj"
st.image(LOGO_URL, width=120)

st.title("🎈 CHE Operational Dashboard")
st.write("Welcome. Pick a tool from the sidebar, or use a link below to get started.")

st.divider()

st.page_link(
    "pages/ticket_extractor.py",
    label="🎫 Ticket Extractor",
    help="Extract passenger, PNR, sector, and flight details from PDF tickets",
)

# Add a link for each new tool here as you build it, e.g.:
# st.page_link("pages/another_tool.py", label="🛠️ Another Tool", help="...")