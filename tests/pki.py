# SPDX-License-Identifier: GPL-3.0-or-later
"""A throw-away CA + signer certificate for the signing tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass
class TestPKI:
    ca_cert: x509.Certificate
    ca_key: rsa.RSAPrivateKey
    signer_cert: x509.Certificate
    signer_key: rsa.RSAPrivateKey

    def asn1(self, cert: x509.Certificate) -> asn1_x509.Certificate:
        return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))

    @property
    def signer_der_key(self) -> bytes:
        return self.signer_key.private_bytes(serialization.Encoding.DER,
                                             serialization.PrivateFormat.TraditionalOpenSSL,
                                             serialization.NoEncryption())


def make_pki(signer_cn: str = "Ion Popescu-Știrbu") -> TestPKI:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pdf45 Test CA"),
                         x509.NameAttribute(NameOID.COUNTRY_NAME, "RO")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, signer_cn),
                                 x509.NameAttribute(NameOID.COUNTRY_NAME, "RO")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=180))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False, crl_sign=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return TestPKI(ca_cert, ca_key, cert, key)
