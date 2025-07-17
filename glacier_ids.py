import pandas as pd
from pathlib import Path

# Path to your repo assets folder
assets_path = Path("GlacierDashboard/assets")

# Output list for dictionary entries
glacier_entries = []

# Loop through each RGI region folder in assets
for region_dir in assets_path.glob("RGI2000-v7.0-G-*"):
    csv_files = list(region_dir.glob("*-attributes.csv"))
    if not csv_files:
        print(f"⚠️ No attribute CSV in {region_dir}")
        continue

    csv_file = csv_files[0]
    try:
        df = pd.read_csv(csv_file)
    except Exception as e:
        print(f"❌ Failed to read {csv_file}: {e}")
        continue

    # Drop rows with missing required info
    df = df.dropna(subset=['area_km2', 'glac_name', 'rgi_id', 'zmean_m'])

    # Get top 10 largest glaciers by area
    top10 = df.sort_values(by='area_km2', ascending=False).head(10)

    for _, row in top10.iterrows():
        name = row['glac_name'].strip().replace(" ", "_") or row['rgi_id']
        rgi_id = row['rgi_id']
        height = int(round(row['zmean_m']))
        glacier_entries.append(f"'{name}': ['{rgi_id}', {height}],")

# Define the output file path
output_file = assets_path / "glacier_ids.txt"

# Write to text file in the assets folder
with open(output_file, "w") as f:
    f.write("glacier_ids = {\n")
    for entry in glacier_entries:
        f.write(f"    {entry}\n")
    f.write("}")

print(f"✅ Glacier dictionary written to: {output_file.resolve()}")
