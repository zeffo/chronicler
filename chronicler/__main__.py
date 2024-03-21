import subprocess

if __name__ == "__main__":
    subprocess.run("litestar --app chronicler.app:app run".split())
