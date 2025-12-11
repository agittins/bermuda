with import <nixpkgs>{};

stdenv.mkDerivation {
	name = "esphome-build";

	nativeBuildInputs = [
		platformio
		python3Packages.pip
		python3Packages.pytest
# 		python3Packages.json
		python3Packages.numpy
		python3Packages.torch
		python3Packages.matplotlib

# 		python3Packages.line-profiler
		py-spy
	];
}

#	nom-shell
#	cd tools
#	./graph.py #to open the example data.json
#	#or give a path
#	./graph.py path_to_data.json
