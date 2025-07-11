import os
import yaml
import subprocess

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
    'Columbia': ['RGI60-01.10689', 1309],
    'Seward': ['RGI2000-01.15261', 1242],
    'Bering': ['RGI2000-01.13485', 1484],
    'Hubbard': ['RGI2000-01.15351', 1909],
    'Logan': ['RGI2000-01.16822', 2330],
    'Kaskawulsh': ['RGI2000-01.16268', 2179],
    'Nabesna': ['RGI2000-01.06338', 2256],
    'Yahtse': ['RGI2000-01.13346', 1431],
    'Klutlan': ['RGI2000-01.16317', 2585],
    'Chitina': ['RGI2000-01.08538', 2537],
    'Klinaklini': ['RGI2000-02.05152', 1696],
    'Franklin': ['RGI2000-02.06836', 2077],
    '': ['RGI2000-02.06848', 2015],
    'Jewakwa': ['RGI2000-02.07266', 2037],
    'Bridge': ['RGI2000-02.04567', 2205],
    'Jacobsen': ['RGI2000-02.05777', 2090],
    'Stanley Smith': ['RGI2000-02.04603', 2344],
    'Talchako': ['RGI2000-02.08338', 1973],
    'Tiedemann': ['RGI2000-02.08287', 1923],
    '': ['RGI2000-02.08002', 1740],
    '': ['RGI2000-03.04210', 927],
    '': ['RGI2000-03.05172', 942],
    '': ['RGI2000-03.05208', 1120],
    '': ['RGI2000-03.04357', 624],
    '': ['RGI2000-03.05107', 643],
    '': ['RGI2000-03.04910', 525],
    'd`Iberville': ['RGI2000-03.03977', 1354],
    '': ['RGI2000-03.04325', 1133],
    'M`Clintock': ['RGI2000-03.01596', 1354],
    '': ['RGI2000-03.04211', 1184],

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

