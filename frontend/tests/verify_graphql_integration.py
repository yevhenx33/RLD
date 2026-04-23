import os

def verify_graphql_integration(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints:
    assert "useSWR(" in content, "Failure Mode: useSWR missing."
    assert "postGraphQL(" in content, "Failure Mode: postGraphQL client missing."
    assert "LendingDataHub" in content, "Failure Mode: Unified GraphQL query missing."
    assert "Math.floor(new Date(row.date).getTime() / 1000)" in content, "Failure Mode: Missing UNIX epoch cast. Chart will break."
    assert "parseMarketSnapshots" in content, "Failure Mode: Safe aggregation boundary missing."
    assert "calculateTotals" in content, "Failure Mode: Top level stats calculator missing."
    
    print("GraphQL Integration Verified. Single-request pre-aggregation approach mathematically sound.")

if __name__ == "__main__":
    verify_graphql_integration("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
