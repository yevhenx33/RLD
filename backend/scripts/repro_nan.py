import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np
import json

def test_nan_replacement():
    df = pd.DataFrame({'a': [1.0, 2.0, np.nan], 'b': [np.nan, "foo", "bar"]})
    print("Original types:")
    print(df.dtypes)
    
    # Method 1: where(pd.notnull(df), None)
    df1 = df.copy()
    df1 = df1.where(pd.notnull(df1), None)
    
    recs1 = df1.to_dict(orient="records")
    print("Method 1 records:", recs1)
    try:
        json.dumps(recs1)
        print("Method 1 JSON: Success")
    except Exception as e:
        print("Method 1 JSON: Failed", e)

    # Method 2: loops
    df2 = df.copy()
    recs2 = df2.to_dict(orient="records")
    for r in recs2:
        for k, v in r.items():
            if isinstance(v, float) and v != v: # is NaN
                r[k] = None
    
    print("Method 2 records:", recs2)
    try:
        json.dumps(recs2)
        print("Method 2 JSON: Success")
    except Exception as e:
        print("Method 2 JSON: Failed", e)

if __name__ == "__main__":
    test_nan_replacement()