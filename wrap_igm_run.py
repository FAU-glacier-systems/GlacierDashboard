import os
import yaml
import subprocess
import time
from netCDF4 import Dataset

glacier_ids = {
    #'Aletsch_Glacier': ['RGI60-11.01450', 2900],
    #'Rhone_Glacier': ['RGI2000', 2950],
    #'Mer_de_Glace': ['RGI60-11.03643', 2900],
    #'Perito_Moreno': ['RGI60-17.00312', 1200],
    #'Schiaparelli': ['RGI60-17.03160', 500],
    #'Kronebreen': ['RGI60-07.01464', 720],
    #'Engabreen': ['RGI60-08.01657', 1000],
    #'Franz Josef': ['RGI60-18.02397', 2167],
    #'Khumbu': ['RGI60-15.03733', 5568],
    #'Columbia': ['RGI60-01.10689', 1309],
    #'Seward': ['RGI2000-v7.0-G-01-15261', 1242],
    #'Bering': ['RGI2000-01.13485', 1484],
    #'Hubbard': ['RGI2000-01.15351', 1909],
    #'Logan': ['RGI2000-01.16822', 2330],
    #'Kaskawulsh': ['RGI2000-01.16268', 2179],
    #'Nabesna': ['RGI2000-01.06338', 2256],
    #'Yahtse': ['RGI2000-01.13346', 1431],
    #'Klutlan': ['RGI2000-01.16317', 2585],
    #'Chitina': ['RGI2000-v7.0-G-01-16307', 2537],
    #'Klinaklini': ['RGI2000-02.05152', 1696],
    #'Franklin': ['RGI2000-02.06836', 2077],
    #'RGI2000-02.06848': ['RGI2000-02.06848', 2015],
    #'Jewakwa': ['RGI2000-02.07266', 2037],
    #'Bridge': ['RGI2000-02.04567', 2205],
    #'Jacobsen': ['RGI2000-02.05777', 2090],
    #'Stanley Smith': ['RGI2000-02.04603', 2344],
    #'Talchako': ['RGI2000-02.08338', 1973],
    #'Tiedemann': ['RGI2000-02.08287', 1923],
    #'RGI2000-02.08002': ['RGI2000-02.08002', 1740],
    #'RGI2000-03.04210': ['RGI2000-03.04210', 927],
    #'RGI2000-03.05172': ['RGI2000-03.05172', 942],
    #'RGI2000-03.05208': ['RGI2000-03.05208', 1120],
    #'RGI2000-03.04357': ['RGI2000-03.04357', 624],
    #'RGI2000-03.05107': ['RGI2000-03.05107', 643],
    #'RGI2000-03.04910': ['RGI2000-03.04910', 525],
    'd`Iberville': ['RGI2000-v7.0-G-03-03977', 1354],
    #'RGI2000-03.04325': ['RGI2000-03.04325', 1133],
    #'M`Clintock': ['RGI2000-03.01596', 1354],
    #'RGI2000-03.04211': ['RGI2000-03.04211', 1184]
}

def main():
    if not os.path.exists('data/'):
        os.mkdir('data/')

    for glacier in glacier_ids:
        data_dir = os.path.join('data', glacier)
        if not os.path.exists(data_dir):
            os.mkdir(data_dir)

        # Ensure experiment directory exists
        experiment_dir = os.path.join(data_dir, 'experiment')
        if not os.path.exists(experiment_dir):
            os.makedirs(experiment_dir)

        rgi_id = glacier_ids[glacier][0]

        # Minimal download_params: NO compute_centerlines
        download_params = {
            "core": {
                "url_data": "",
                "hardware": {
                    "visible_gpus": []
                }
            },
            "defaults": [
                {"override /inputs": ["oggm_shop"]}
            ],
            "inputs": {
                "oggm_shop": {
                    "RGI_ID": rgi_id,
                    "thk_source": "millan_ice_thickness"
                }
            }
        }


        # Write download_param.yaml
        param_path = os.path.join(experiment_dir, 'download_param.yaml')
        with open(param_path, 'w') as f:
            f.write("# @package _global_\n\n")
            yaml.dump(download_params, f)

        # Run the download step
        result = subprocess.run(
            "igm_run +experiment=download_param",
            shell=True,
            cwd=data_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(result.stdout.decode())
        print(result.stderr.decode())
        if result.returncode != 0:
            print(f"Download step failed for glacier {glacier}.")
            continue

        # Wait up to 4 minutes for input.nc
        input_nc_path = os.path.join(data_dir, "data", "input.nc")
        wait_time = 0
        wait_interval = 5
        max_wait = 600
        while not os.path.exists(input_nc_path) and elapsed < wait_time:
            print(f"Waiting for input.nc to be created... ({elapsed}/{wait_time}s)")
            time.sleep(wait_interval)
            elapsed += wait_interval

        if not os.path.exists(input_nc_path):
            print(f"WARNING: input.nc was not created for glacier {glacier}. Skipping.")
            continue

        # Check if thkinit exists in input.nc
        try:
            with Dataset(input_nc_path, "r") as nc:
                if "thkinit" not in nc.variables:
                    print(f"WARNING: 'thkinit' variable is missing in input.nc for glacier {glacier}. Skipping.")
                    continue
                else:
                    print(f"'thkinit' variable found in input.nc for glacier {glacier}. Proceeding.")
        except Exception as e:
            print(f"ERROR: Could not read input.nc for glacier {glacier}: {e}")
            continue

        # Now run the iterations
        for temp in range(5):
            print(f"Iteration {temp}: Using values from final_ensemble")

            ela = glacier_ids[glacier][1]
            run_params = {
                "core": {
                    "url_data": "",
                    "hardware": {
                        "visible_gpus": []
                    }
                },
                "defaults": [
                    {"override /inputs": ["load_ncdf"]},
                    {"override /processes": ["smb_simple", "iceflow", "time", "thk"]},
                    {"override /outputs": ["write_ncdf"]}
                ],
                "inputs": {
                    "load_ncdf": {
                        "input_file": "input.nc"
                    }
                },
                "processes": {
                    "smb_simple": {
                        "array": [
                            ["time", "gradabl", "gradacc", "ela", "accmax"],
                            [2000, 0.008, 0.002, ela, 5.0],
                            [2100, 0.008, 0.002, ela + 100 * temp, 5.0]
                        ]
                    },
                    "time": {
                        "start": 2000,
                        "end": 2100
                    }
                },
                "outputs": {
                    "write_ncdf": {
                        "output_file": f"output_{temp}.nc"
                    }
                },
                "hydra": {
                    "run": {
                        "dir": "outputs"
                    }
                }
            }

            # Write run_params.yaml
            param_path = os.path.join(experiment_dir, 'run_params.yaml')
            with open(param_path, 'w') as f:
                f.write("# @package _global_\n\n")
                yaml.dump(run_params, f)

            # Run the model
            result = subprocess.run(
                "igm_run +experiment=run_params",
                shell=True,
                cwd=data_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            print(result.stdout.decode())
            print(result.stderr.decode())
            if result.returncode != 0:
                print(f"Run step failed for glacier {glacier} iteration {temp}.")
                continue


if __name__ == '__main__':
    main()