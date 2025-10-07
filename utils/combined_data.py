import os
import pandas as pd



def combine_data():
    file1 = "output/autoscout24_data_sample_for_client.xlsx"
    file2 = "output/mobile_data_sample_for_client.xlsx"

    # Read both files
    df1 = pd.read_excel(os.path.join(os.getcwd(), file1) , engine='openpyxl')
    df2 = pd.read_excel(os.path.join(os.getcwd(), file2) , engine='openpyxl')

    # Combine them (stack rows)
    combined_df = pd.concat([df1, df2], ignore_index=True)

    # Optional: remove duplicates if needed
    # combined_df = combined_df.drop_duplicates()

    # Optional: reset index
    combined_df.reset_index(drop=True, inplace=True)

    # ✅ Save combined result
    combined_df.to_excel(os.path.join(os.getcwd(), "output/combined_data.xlsx"), index=False)
    # or to CSV:
    # combined_df.to_csv("combined_data.csv", index=False)

    print("✅ Combined file created successfully!")
    print(f"Total rows: {len(combined_df)}")


# combine_data()
