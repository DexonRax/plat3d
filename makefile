all:
	python grm-transpiler.py plat3d.grm && gcc plat3d.c -o plat3d.exe -static -lraylib -lm -lwinmm -ldwmapi -luser32 -lgdi32 -lopengl32 && ./plat3d.exe