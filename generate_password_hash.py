"""
Run this LOCALLY (not in Streamlit Cloud) to generate the value that goes into
secrets.toml for a teammate's password. This never needs to be committed to your
repo or run anywhere but your own machine.

Usage:
    python generate_password_hash.py
    (it will prompt you for a password, and print the line to paste into secrets.toml)
"""
import hashlib
import getpass
import os


def hash_password(password):
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${digest}"


if __name__ == "__main__":
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden as you type): ")
    hashed = hash_password(password)
    print()
    print("Add this line under the [credentials] section in Streamlit Cloud's Secrets panel:")
    print(f'{username} = "{hashed}"')
