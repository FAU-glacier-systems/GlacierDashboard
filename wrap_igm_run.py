import os
import yaml
import subprocess

glacier_ids = {
    'Aletsch_Glacier': ['RGI60-11.01450', 2900],
    'Rhone_Glacier': ['RGI2000', 2950],
    'Mer_de_Glace': ['RGI60-11.03643', 2900],
    'Perito_Moreno': ['RGI60-17.00312', 1200],
    'Schiaparelli': ['RGI60-17.03160', 500],
    'Kronebreen': ['RGI60-07.01464', 720],
    'Engabreen': ['RGI60-08.01657', 1000],
    'Franz Josef': ['RGI60-18.02397', 2167],
    'Khumbu': ['RGI60-15.03733', 5568],
    'Columbia': ['RGI60-01.10689', 1309]
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
        download_params = {
            "core": {
                "url_data": ""
            },
            "defaults": [
                {"override /inputs": ["oggm_shop", "local"]},
                {"override /processes": []},
                {"override /outputs": []}
            ],
            "inputs": {"oggm_shop": {"RGI_ID": rgi_id}},
            "processes": {},
            "outputs": {}
        }

        param_path = os.path.join(experiment_dir, 'download_param.yaml')
        with open(param_path, 'w') as f:
            f.write("# @package _global_\n\n")
            yaml.dump(download_params, f)

        subprocess.run("igm_run +experiment=download_param", shell=True, cwd=data_dir)

        for temp in range(5):
            print(f"Iteration {temp}: Using values from final_ensemble")

            ela = glacier_ids[glacier][1]
            run_params = {
                "core": {
                    "url_data": ""
                },
                "defaults": [
                    {"override /inputs": ["load_ncdf"]},
                    {"override /processes": ["smb_simple", "iceflow", "time", "thk"]},
                    {"override /outputs": ["write_ncdf"]}
                ],
                "inputs": {"load_ncdf": {"input_file": "input.nc"}},
                "processes": {
                    "smb_simple": {
                        "array": [
                            ["time", "gradabl", "gradacc", "ela", "accmax"],
                            [2000, 0.008, 0.002, ela, 5.0],
                            [2100, 0.008, 0.002, ela + 100 * temp, 5.0]
                        ]
                    },
                    "time": {"start": 2000, "end": 2100}
                },
                "outputs": {"write_ncdf": {"output_file": f"output_{temp}.nc"}},
                "hydra": {"run": {"dir": "outputs"}}
            }

            param_path = os.path.join(experiment_dir, 'run_params.yaml')
            with open(param_path, 'w') as f:
                f.write("# @package _global_\n\n")
                yaml.dump(run_params, f)

            subprocess.run("igm_run +experiment=run_params", shell=True, cwd=data_dir)


if __name__ == '__main__':
    main()

