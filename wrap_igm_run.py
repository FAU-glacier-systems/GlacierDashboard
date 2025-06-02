import numpy as np
import matplotlib.pyplot as plt
import os
import json

glacier_ids = {#'Aletsch_Glacier': ['RGI60-11.01450', 2900],
               'Rhone_Glacier': ['RGI2000', 2950],
               #'Mer_de_Glace': ['RGI60-11.03643', 2900],
               #'Perito_Moreno': ['RGI60-17.00312', 1200],
               #'Schiaparelli': ['RGI60-17.03160', 500],
               #'Kronebreen': ['RGI60-07.01464', 720],
               #'Engabreen':['RGI60-08.01657', 1000],
               # 'Franz Josef': ['RGI60-18.02397', 2167],
                #'Khumbu': ['RGI60-15.03733', 5568],
                #'Columbia': ['RGI60-01.10689', 1309]


 }


def main():
    if not os.path.exists('data/'):
        os.mkdir('data/')
    for glacier in glacier_ids:
        data_dir = 'data/' + glacier + '/'
        if not os.path.exists(data_dir):
            os.mkdir(data_dir)

        ela = glacier_ids[glacier][1]
        download_param = {
            "modules_preproc": ["oggm_shop"],
            "modules_process": [],
            "modules_postproc": [],
            "oggm_RGI_ID": glacier_ids[glacier][0],
        }
        param_path = data_dir + 'download_param.json'
        with open(param_path, 'w') as f:
            json.dump(download_param, f)

        os.chdir(data_dir)
        print(os.getcwd())
        #os.system("igm_run --param_file download_param.json")


        # Load data from results_.json
        with open('result_seed_1_1.0_50.json', 'r') as f:
            results_data = json.load(f)

        # Extract the "final_ensemble" list
        final_ensemble = sorted(results_data.get("final_ensemble", []), key=lambda x: x[0])


        # Ensure we have enough data for each iteration
        if len(final_ensemble) < 6:
            raise ValueError("Not enough data in 'final_ensemble' for 6 iterations.")

        # Loop through each index and update the parameters accordingly
        for temp in range(6):  # Equivalent to [0, 1, 2, 3, 4, 5]
            print(f"Iteration {temp}: Using values from final_ensemble")

            # Extract the values for this iteration
            #ela_value, gradabl_value, gradacc_value = final_ensemble[temp]

            # Prepare the dictionary with updated values
            ela = glacier_ids[glacier][1]
            data = {
                "modules_preproc": ["load_ncdf"],
                "modules_process": ["smb_simple", "iceflow", "time", "thk"],
                "modules_postproc": ["write_ncdf", "print_info"],
                "smb_simple_array": [
                    ["time", "gradabl", "gradacc", "ela", "accmax"],
                    [2000, 8/1000, 2/1000, ela,
                     5.0],
                    [2100, 8/1000, 2/1000, ela + 100 * temp, 5.0]
                ],
                "lncd_input_file": 'output_saved.nc',
                "time_start": 2000,
                "time_end": 2100,
                "wncd_output_file": f"output_{temp}.nc"
            }

            # Write the updated parameters to run_params.json
            with open('run_params.json', 'w') as f:
                json.dump(data, f)

            # Execute the command
            os.system("igm_run --param_file run_params.json")


        os.chdir('../../')


if __name__ == '__main__':
    main()
