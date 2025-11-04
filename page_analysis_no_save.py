import pandas as pd
import requests
from PyPDF2 import PdfReader
from io import BytesIO
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create session with user agent
session = requests.Session()
session.headers.update({
    'User-Agent': os.getenv('USER_AGENT')
})

def count_pdf_pages(url):
    """Download PDF and return page count"""
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        pdf_data = BytesIO(response.content)
        pdf_reader = PdfReader(pdf_data)
        return len(pdf_reader.pages)
    except Exception as e:
        print(f"Error with {url}: {str(e)}")
        return None

def process_pdfs(df):
    """Process all PDFs in dataframe and return results"""
    results = []
    
    for idx, row in df.iterrows():
        url = row['pdf_url']
        print(f"Processing {idx+1}/{len(df)}: {url}")
        
        page_count = count_pdf_pages(url)
        results.append({
            'pdf_url': url,
            'page_count': page_count,
            'status': 'success' if page_count else 'failed'
        })
        
        time.sleep(0.5)  # Brief pause between requests
    
    return pd.DataFrame(results)

# Load your dataframe
# df = pd.read_csv('your_file.csv')  # or however you're loading it
df = pd.read_excel('pdf_assets.xlsx')

# Process PDFs
results_df = process_pdfs(df)

# Create summary statistics
summary = {
    'Total PDFs': len(results_df),
    'Successful': (results_df['status'] == 'success').sum(),
    'Failed': (results_df['status'] == 'failed').sum(),
    'Total Pages': results_df['page_count'].sum(),
    'Average Pages': results_df['page_count'].mean(),
    'Min Pages': results_df['page_count'].min(),
    'Max Pages': results_df['page_count'].max(),
    'Median Pages': results_df['page_count'].median()
}

summary_df = pd.DataFrame([summary])

# Export to Excel with both sheets
with pd.ExcelWriter('pdf_page_counts.xlsx', engine='openpyxl') as writer:
    results_df.to_excel(writer, sheet_name='Details', index=False)
    summary_df.to_excel(writer, sheet_name='Summary', index=False)

print("\n" + "="*50)
print("SUMMARY")
print("="*50)
for key, value in summary.items():
    print(f"{key}: {value}")
print("\nResults saved to: pdf_page_counts.xlsx")