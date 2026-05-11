import json
import os

def extract_keys(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return list(data.keys())
    except Exception as e:
        return f"Error reading {filename}: {e}"

with open('keys.txt', 'w') as f:
    f.write("Hazards: " + str(extract_keys('data/hazards.json')) + "\n")
    f.write("Items: " + str(extract_keys('data/items.json')) + "\n")
