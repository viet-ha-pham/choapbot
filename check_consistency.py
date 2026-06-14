from owlready2 import *

onto = get_ontology(r"E:\WORK\ONTOLOGY\sumo.owl").load()

Human = onto.Human
Process = onto.Process
print(Human)
with onto:
    bad = Human()
    bad.is_a.append(Process)

try:
    sync_reasoner([onto], debug=2)
    print("Ontology is consistent")
except OwlReadyInconsistentOntologyError:
    print("Inconsistent ontology detected")