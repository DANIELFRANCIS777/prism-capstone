"""Mints the secrets a production deployment needs.

    python -m app.keygen

Prints a JWT RS256 key pair and a Fernet credential-encryption key, ready to
paste into a platform's secret store (Render env vars, Fly secrets, k8s
Secret, ...). Nothing is written to disk - this deliberately does not touch
the local keys/ directory, so running it can never overwrite a key that
existing data depends on.

Generate these ONCE per environment and keep them. Rotating the credential
encryption key makes every stored BYOK credential unreadable; rotating the
JWT pair logs everyone out.
"""

from app.credential_crypto import generate_key
from app.jwt_keys import generate_key_pair


def main() -> None:
    private_pem, public_pem = generate_key_pair()

    print("# Prism deployment secrets - store these in your platform's secret manager.")
    print("# Generate once per environment; losing them is not recoverable.\n")

    print("CREDENTIAL_ENCRYPTION_KEY=" + generate_key())
    print()
    # Multi-line PEMs: most platforms accept a literal newline in a secret
    # value; the \n-escaped form below is for the ones that don't.
    print("JWT_PRIVATE_KEY=" + repr(private_pem)[1:-1])
    print()
    print("JWT_PUBLIC_KEY=" + repr(public_pem)[1:-1])


if __name__ == "__main__":
    main()
