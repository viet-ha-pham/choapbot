import streamlit as st
import pickle
import time

with open("vie_cho_dct_2023.pkl", "rb") as f:
    vie_cho_dct = pickle.load(f)


with open("keywords.txt", "r", encoding="utf-8") as f:
    lines = f.readlines()

keywords = [ l.strip() for l in lines if l.strip() ]


def trans(txt):
    sp = txt.split()
    return " ".join([ vie_cho_dct[w] if w in vie_cho_dct else w for w in sp]).replace("lồn","noòng").replace("khỏe","tãy")


def get_intent(inp):
    inp = inp.lower()
    for k in keywords:
        if k in inp:
            return k
    return "tâm sự"


def take_notes(inp):
    inp = inp.lower()
    intent = get_intent(inp)
    for k in keywords:
        if intent == k:
            with open(f"{k}.txt","w", encoding="utf-8") as f:
                f.write(inp + "\n")


def save():
    msg = st.session_state.messages[-2]["content"]
    msg += ": "
    msg += st.session_state.messages[-1]["content"]
    with open("hay.txt", "w", encoding="utf-8") as f:
        f.write(msg + "\n")


def clear_chat_history():
    st.session_state.messages = []

def response_generator(prompt):
    response = trans(prompt)
    for word in response.split():
        yield word + " "
        time.sleep(0.05)


st.sidebar.title("Trợ lý ảo giúp tự thấu hiểu nội tâm")
st.sidebar.write("Hãy bày tỏ tâm sự của mình và dùng những từ khóa sau để ghi chú:")
st.sidebar.markdown("\n".join([f"+ {k}" for k in keywords]))

st.sidebar.button("Xóa lịch sử chat", on_click=clear_chat_history)

with st.chat_message("assistant"):
    st.markdown("Hãy tâm sự với tôi đi!")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Hãy nói tâm sự của bạn"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        response = st.write_stream(response_generator(prompt))
        st.button("Câu này hay", on_click=save)

    st.session_state.messages.append({"role": "assistant", "content": response})
    take_notes(prompt)