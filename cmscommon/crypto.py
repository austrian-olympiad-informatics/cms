#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2017 Valentin Rosca <rosca.valentin2012@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Utilities dealing with encryption and randomness."""

import binascii
import random
from string import ascii_lowercase
import hmac

import bcrypt
from Crypto import Random
from Crypto.Cipher import AES

from cmscommon.binary import bin_to_hex, hex_to_bin, bin_to_b64, b64_to_bin


__all__ = [
    "get_random_key", "get_hex_random_key",

    "encrypt_binary", "decrypt_binary",
    "encrypt_number", "decrypt_number",

    "generate_random_password",

    "validate_password", "build_password", "hash_password",
    "parse_authentication",
    ]


_RANDOM = Random.new()


def get_random_key():
    """Generate 16 random bytes, safe to be used as AES key.

    """
    return _RANDOM.read(16)


def get_hex_random_key():
    """Generate 16 random bytes, safe to be used as AES key.
    Return it encoded in hexadecimal.

    """
    return bin_to_hex(get_random_key())


def encrypt_binary(pt, key_hex):
    """Encrypt the plaintext with the 16-bytes key.

    A random salt is added to avoid having the same input being
    encrypted to the same output.

    pt (bytes): the "plaintext" to encode.
    key_hex (str): a 16-bytes key in hex (a string of 32 hex chars).

    return (str): pt encrypted using the key, in a format URL-safe
        (more precisely, base64-encoded with alphabet "a-zA-Z0-9.-_").

    """
    key = hex_to_bin(key_hex)
    # Pad the plaintext to make its length become a multiple of the block size
    # (that is, for AES, 16 bytes), using a byte 0x01 followed by as many bytes
    # 0x00 as needed. If the length of the message is already a multiple of 16
    # bytes, add a new block.
    pt_pad = pt + b'\01' + b'\00' * (16 - (len(pt) + 1) % 16)
    # The IV is a random block used to differentiate messages encrypted with
    # the same key. An IV should never be used more than once in the lifetime
    # of the key. In this way encrypting the same plaintext twice will produce
    # different ciphertexts.
    iv = get_random_key()
    # Initialize the AES cipher with the given key and IV.
    aes = AES.new(key, AES.MODE_CBC, iv)
    ct = aes.encrypt(pt_pad)
    # Convert the ciphertext in a URL-safe base64 encoding
    ct_b64 = bin_to_b64(iv + ct)\
        .replace('+', '-').replace('/', '_').replace('=', '.')
    return ct_b64


def decrypt_binary(ct_b64, key_hex):
    """Decrypt a ciphertext generated by encrypt_binary.

    ct_b64 (str): the ciphertext as produced by encrypt_binary.
    key_hex (str): the 16-bytes key in hex format used to encrypt.

    return (bytes): the plaintext.

    raise (ValueError): if the ciphertext is invalid.

    """
    key = hex_to_bin(key_hex)
    try:
        # Convert the ciphertext from a URL-safe base64 encoding to a
        # bytestring, which contains both the IV (the first 16 bytes) as well
        # as the encrypted padded plaintext.
        iv_ct = b64_to_bin(
            ct_b64.replace('-', '+').replace('_', '/').replace('.', '='))
        aes = AES.new(key, AES.MODE_CBC, iv_ct[:16])
        # Get the padded plaintext.
        pt_pad = aes.decrypt(iv_ct[16:])
        # Remove the padding.
        # TODO check that the padding is correct, i.e. that it contains at most
        # 15 bytes 0x00 preceded by a byte 0x01.
        pt = pt_pad.rstrip(b'\x00')[:-1]
        return pt
    except (TypeError, binascii.Error):
        raise ValueError('Could not decode from base64.')
    except ValueError:
        raise ValueError('Wrong AES cryptogram length.')


def encrypt_number(num, key_hex):
    """Encrypt an integer number, with the same properties as
    encrypt_binary().

    """
    hexnum = b"%x" % num
    return encrypt_binary(hexnum, key_hex)


def decrypt_number(enc, key_hex):
    """Decrypt an integer number encrypted with encrypt_number().

    """
    return int(decrypt_binary(enc, key_hex), 16)


def generate_random_password():
    """Utility method to generate a random password.

    return (str): a random string.

    """
    return "".join((random.choice(ascii_lowercase) for _ in range(6)))


def parse_authentication(authentication):
    """Split the given method:password field into its components.

    authentication (str): an authentication string as stored in the DB,
        for example "plaintext:password".

    return (str, str): the method and the payload

    raise (ValueError): when the authentication string is not valid.

    """
    method, sep, payload = authentication.partition(":")

    if sep != ":":
        raise ValueError("Authentication string not parsable.")

    return method, payload


def validate_password(authentication, password):
    """Validate the given password for the required authentication.

    authentication (str): an authentication string as stored in the db,
        for example "plaintext:password".
    password (str): the password provided by the user.

    return (bool): whether password is correct.

    raise (ValueError): when the authentication string is not valid or
        the method is not known.

    """
    method, payload = parse_authentication(authentication)
    if method == "bcrypt":
        password = password.encode('utf-8')
        hashed = payload.encode('utf-8')
        return bcrypt.checkpw(password, hashed)
    elif method == "plaintext":
        return hmac.compare_digest(password, payload)
    else:
        raise ValueError("Authentication method not known.")


def build_password(password, method="plaintext"):
    """Build an auth string from an already-hashed password.

    password (str): the hashed password.
    method (str): the hasing method to use.

    return (str): the string embedding the method and the password.

    """
    # TODO make sure it's a valid bcrypt hash if method is bcrypt.
    return "%s:%s" % (method, password)


def hash_password(password, method="bcrypt"):
    """Hash and build an auth string from a plaintext password.

    password (str): the password in plaintext.
    method (str): the hashing method to use.

    return (str): the auth string containing the hashed password.

    raise (ValueError): if the method is not supported.

    """
    if method == "bcrypt":
        password = password.encode('utf-8')
        payload = bcrypt.hashpw(password, bcrypt.gensalt()).decode('ascii')
    elif method == "plaintext":
        payload = password
    else:
        raise ValueError("Authentication method not known.")

    return build_password(payload, method)
