from owlready2 import *
import re

onto = get_ontology(r"E:\WORK\ONTOLOGY\sumo.owl").load()

# Optional: nếu file lớn quá thì bỏ dòng này
# sync_reasoner_pellet(infer_property_values=True, infer_data_property_values=True)

def find_class(name):
    return onto.search_one(iri=f"*{name}") or onto.search_one(label=name)

SUMO = {
    "Human": find_class("Human"),
    "Process": find_class("Process"),
    "Agent": find_class("Agent") or find_class("AutonomousAgent"),
    "Object": find_class("Object"),
    "Attribute": find_class("Attribute"),
    "DiseaseOrSyndrome": find_class("DiseaseOrSyndrome") or find_class("Disease"),
    "Organization": find_class("Organization"),
}

print(SUMO)

# Từ vựng demo: entity trong câu -> SUMO class
LEXICON = {
    "john": "Human",
    "mary": "Human",
    "temperature": "Attribute",
    "flu": "DiseaseOrSyndrome",
    "hospital": "Organization",
    "meeting": "Process",
    "mri scan": "Process",
}

# Relation constraint: relation -> expected subject/object SUMO class
RELATION_RULES = {
    "attend": {
        "subject": "Human",
        "object": "Process",
    },
    "perform": {
        "subject": "Agent",
        "object": "Process",
    },
    "diagnose": {
        "subject": "Agent",
        "object": "DiseaseOrSyndrome",
    },
}

def is_subclass_of(child_name, parent_name):
    child = SUMO.get(child_name)
    parent = SUMO.get(parent_name)

    if child is None or parent is None:
        print("Missing:", child_name, child, parent_name, parent)
        return False

    ancestor_names = {a.name for a in child.ancestors()}
    return parent.name in ancestor_names

def normalize(text):
    return text.lower().strip().replace(".", "")

def extract_simple_claim(sentence):
    """
    Demo parser cực đơn giản:
    'john attend meeting'
    'temperature attend meeting'
    'flu perform mri scan'
    """
    s = normalize(sentence)

    for rel in RELATION_RULES:
        pattern = rf"(.+?)\s+{rel}s?\s+(.+)"
        m = re.match(pattern, s)
        if m:
            return {
                "subject": m.group(1).strip(),
                "relation": rel,
                "object": m.group(2).strip(),
            }

    return None

def check_sentence(sentence):
    claim = extract_simple_claim(sentence)

    if not claim:
        return {
            "sentence": sentence,
            "status": "UNKNOWN",
            "errors": ["Cannot extract simple claim"],
        }

    subj = claim["subject"]
    rel = claim["relation"]
    obj = claim["object"]

    errors = []

    subj_type = LEXICON.get(subj)
    obj_type = LEXICON.get(obj)

    if subj_type is None:
        errors.append(f"Unknown subject entity: {subj}")

    if obj_type is None:
        errors.append(f"Unknown object entity: {obj}")

    if errors:
        return {
            "sentence": sentence,
            "claim": claim,
            "status": "UNKNOWN",
            "errors": errors,
        }

    rule = RELATION_RULES[rel]
    expected_subj = rule["subject"]
    expected_obj = rule["object"]

    if not is_subclass_of(subj_type, expected_subj):
        errors.append(
            f"Subject sanity violation: '{subj}' is {subj_type}, "
            f"but relation '{rel}' expects {expected_subj}"
        )

    if not is_subclass_of(obj_type, expected_obj):
        errors.append(
            f"Object sanity violation: '{obj}' is {obj_type}, "
            f"but relation '{rel}' expects {expected_obj}"
        )

    return {
        "sentence": sentence,
        "claim": claim,
        "subject_type": subj_type,
        "object_type": obj_type,
        "expected_subject": expected_subj,
        "expected_object": expected_obj,
        "status": "BAD" if errors else "OK",
        "errors": errors,
    }

tests = [
    "John attend meeting.",
    "Temperature attend meeting.",
    "Flu perform MRI scan.",
    "Hospital perform MRI scan.",
    "John diagnose flu.",
    "Temperature diagnose flu.",
]

for t in tests:
    result = check_sentence(t)
    print("\n---")
    print("Sentence:", result["sentence"])
    print("Status:", result["status"])
    if "claim" in result:
        print("Claim:", result["claim"])
        print("Types:", result.get("subject_type"), "->", result.get("object_type"))
    for e in result["errors"]:
        print(" -", e)


Human = find_class("Human")
Agent = find_class("Agent")
AutonomousAgent = find_class("AutonomousAgent")

print("Human:", Human)
print("Agent:", Agent)
print("AutonomousAgent:", AutonomousAgent)

print("Human ancestors:")
for a in Human.ancestors():
    print(a)

print("Human <= Agent?", Agent in Human.ancestors())
print("Human <= AutonomousAgent?", AutonomousAgent in Human.ancestors())

Human = find_class("Human")

print("Human =", Human)

print("\nDirect parents:")
for p in Human.is_a:
    print(" ", p)

print("\nAncestors:")
for a in Human.ancestors():
    print(" ", a)