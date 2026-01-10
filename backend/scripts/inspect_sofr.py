import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))


import pandas as pd
import os

file_path = "SOFR.xlsx"
if not os.path.exists(file_path):
    # Try one level up if we are in backend
    file_path = "../SOFR.xlsx"

try:
    df = pd.read_excel(file_path)
    print("Columns:", df.columns.tolist())
    print("First 5 rows:")
    print(df.head())
    print("Data Types:")
    print(df.dtypes)
except Exception as e:
    print(f"Error reading file {file_path}: {e}")