import sys
import subprocess
import importlib.util

    # Aceste librarii standard pot fi actualizate automat in siguranta.
    # Am exclus intentionat "torch", "torchvision", "torchaudio" si "timesfm" 
    # pentru a preveni riscul de a se suprascrie versiunea de GPU (cu124/cu128) cu versiunea standard de CPU.
SAFE_UPGRADE_PACKAGES = [
    "streamlit",
    "pandas",
    "numpy",
    "numba",
    "psutil",
    "requests",
]

def check_and_upgrade(packages):
    print("\n--- Verificare si Actualizare Librarii Standard ---")
    try:
        # Apelam pip pentru a verifica si actualiza la ultima versiune stabila
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + packages
        print(f"Rulam comanda de actualizare in fundal...")
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        print("-> [OK] Librariile standard sunt actualizate si perfect compatibile!\n")
    except subprocess.CalledProcessError as e:
        print(f"-> [EROARE] Nu s-au putut actualiza librariile standard. Asigura-te ca ai internet: {e}\n")
    except Exception as e:
        print(f"-> [EROARE] A aparut o problema: {e}\n")

def check_pytorch():
    print("--- Verificare Mediu de Inteligenta Artificiala (PyTorch & TimesFM) ---")
    
    # 1. Verificare PyTorch
    try:
        import torch
        version = getattr(torch, '__version__', 'Necunoscuta')
        print(f"-> [OK] PyTorch este instalat (Versiune: {version})")
        
        # 2. Verificare Suport GPU (CUDA)
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else "NVIDIA GPU"
            print(f"-> [EXCELENT] Suport GPU ACTIVAT pe placa video: {gpu_name}!")
            print("   (Am blocat actualizarea automata la PyTorch tocmai pentru a pastra aceasta viteza!)")
        else:
            print("-> [ATENTIE] PyTorch functioneaza, dar foloseste doar CPU (procesorul normal).")
            print("   Daca ai placa video NVIDIA si vrei sa mearga foarte rapid predictiile, instaleaza manual:")
            print("   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124")
    except ImportError:
        print("-> [EROARE CRITICA] PyTorch NU este instalat! Pentru a-l instala cu suport de GPU, ruleaza comanda:")
        print("   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124")
        
    print("")

    # 3. Verificare TimesFM
    try:
        import timesfm
        version = getattr(timesfm, '__version__', 'Necunoscuta')
        print(f"-> [OK] Motorul Google TimesFM este instalat (Versiune: {version})")
    except ImportError:
        print("-> [EROARE] Libraria timesfm lipseste! O poti instala cu comanda: pip install timesfm")

def _is_in_venv() -> bool:
    """Detecteaza daca Python-ul curent ruleaza intr-un virtualenv."""
    return (
        hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
        or "venv" in sys.executable.lower()
    )


def main():
    import os
    print(f"Versiune Python activa: {sys.version.split()[0]}")
    print(f"Executabil:             {sys.executable}")
    print(f"In virtualenv:          {'DA' if _is_in_venv() else 'NU (Python global)'}\n")

    if not _is_in_venv():
        print("[ATENTIE] Acest script ruleaza in Python-ul GLOBAL al sistemului,")
        print("          nu in venv-ul proiectului. Rezultatele de mai jos pot fi")
        print(f"          IRELEVANTE pentru aplicatie!")
        print(f"          Foloseste ACTUALIZARI.bat (nu 'python verifica_mediu.py'")
        print(f"          direct) ca sa verifici venv-ul corect: .venv_{os.environ.get('COMPUTERNAME', '<MASINA>')}.\n")

    # Executam logic verificarile
    check_pytorch()
    check_and_upgrade(SAFE_UPGRADE_PACKAGES)

    print("===================================================================")
    print("                      VERIFICARE FINALIZATA                        ")
    print("   Daca nu au aparut [ERORI] mai sus, aplicatia este gata de start.")
    print("===================================================================")

if __name__ == "__main__":
    main()
