TASK_ONTOLOGY = {
    "Task": "SummarizeDocument",

    "Steps": {
        "LoadDocument": {
            "produces": ["document"],
            "next": ["CleanDocument"]
        },
        "CleanDocument": {
            "requires": ["document"],
            "produces": ["clean_document"],
            "next": ["ExtractKeyPoints"]
        },
        "ExtractKeyPoints": {
            "requires": ["clean_document"],
            "produces": ["key_points"],
            "next": ["GenerateSummary"]
        },
        "GenerateSummary": {
            "requires": ["key_points"],
            "produces": ["summary"],
            "next": ["CheckQuality"]
        },
        "CheckQuality": {
            "requires": ["summary"],
            "produces": ["quality_score"],
            "next": ["END"]
        }
    }
}

from typing import TypedDict
from langgraph.graph import StateGraph, END


class SummaryState(TypedDict, total=False):
    raw_text: str
    document: str
    clean_document: str
    key_points: list[str]
    summary: str
    quality_score: float


def load_document(state: SummaryState):
    return {
        "document": state["raw_text"]
    }


def clean_document(state: SummaryState):
    text = state["document"].strip()
    text = " ".join(text.split())
    return {
        "clean_document": text
    }


def extract_key_points(state: SummaryState):
    text = state["clean_document"]
    sentences = text.split(".")
    key_points = [s.strip() for s in sentences if s.strip()][:3]
    return {
        "key_points": key_points
    }


def generate_summary(state: SummaryState):
    return {
        "summary": " ".join(state["key_points"])
    }


def check_quality(state: SummaryState):
    summary = state["summary"]
    score = min(len(summary) / 200, 1.0)
    return {
        "quality_score": score
    }

NODE_REGISTRY = {
    "LoadDocument": load_document,
    "CleanDocument": clean_document,
    "ExtractKeyPoints": extract_key_points,
    "GenerateSummary": generate_summary,
    "CheckQuality": check_quality,
}

def build_graph_from_task_ontology(ontology):
    graph = StateGraph(SummaryState)

    steps = ontology["Steps"]

    # Add nodes
    for step_name in steps:
        graph.add_node(step_name, NODE_REGISTRY[step_name])

    # Set entry point
    first_step = list(steps.keys())[0]
    graph.set_entry_point(first_step)

    # Add edges
    for step_name, step_info in steps.items():
        for next_step in step_info["next"]:
            if next_step == "END":
                graph.add_edge(step_name, END)
            else:
                graph.add_edge(step_name, next_step)

    return graph.compile()

app = build_graph_from_task_ontology(TASK_ONTOLOGY)

result = app.invoke({
    "raw_text": """
    LangGraph is useful for building stateful AI workflows.
    Ontologies can describe task structure.
    A task ontology can help connect nodes automatically.
    This makes agent workflows more explicit and reusable.
    """
})

print(result)

# {
#     "raw_text": "...",
#     "document": "...",
#     "clean_document": "...",
#     "key_points": [
#         "LangGraph is useful for building stateful AI workflows",
#         "Ontologies can describe task structure",
#         "A task ontology can help connect nodes automatically"
#     ],
#     "summary": "LangGraph is useful ... automatically",
#     "quality_score": 1.0
# }