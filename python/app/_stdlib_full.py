"""Bundle the FULL Python stdlib into the frozen sidecar.

v4.18.2: PyInstaller only traces stdlib modules reachable from the app's
import graph. Engine deps import stdlib dynamically at runtime
(sentence-transformers/torch -> filecmp, ...), so the frozen sidecar raised
ModuleNotFoundError and engines were "installed but unusable" (embeddings
503 live-verified). Explicit static imports below make PyInstaller collect
every stdlib module; platform-specific ones are guarded.
"""

try:
    import __future__  # noqa: F401
except Exception:
    pass
try:
    import _abc  # noqa: F401
except Exception:
    pass
try:
    import _aix_support  # noqa: F401
except Exception:
    pass
try:
    import _ast  # noqa: F401
except Exception:
    pass
try:
    import _asyncio  # noqa: F401
except Exception:
    pass
try:
    import _bisect  # noqa: F401
except Exception:
    pass
try:
    import _blake2  # noqa: F401
except Exception:
    pass
try:
    import _bz2  # noqa: F401
except Exception:
    pass
try:
    import _codecs  # noqa: F401
except Exception:
    pass
try:
    import _codecs_cn  # noqa: F401
except Exception:
    pass
try:
    import _codecs_hk  # noqa: F401
except Exception:
    pass
try:
    import _codecs_iso2022  # noqa: F401
except Exception:
    pass
try:
    import _codecs_jp  # noqa: F401
except Exception:
    pass
try:
    import _codecs_kr  # noqa: F401
except Exception:
    pass
try:
    import _codecs_tw  # noqa: F401
except Exception:
    pass
try:
    import _collections  # noqa: F401
except Exception:
    pass
try:
    import _collections_abc  # noqa: F401
except Exception:
    pass
try:
    import _compat_pickle  # noqa: F401
except Exception:
    pass
try:
    import _compression  # noqa: F401
except Exception:
    pass
try:
    import _contextvars  # noqa: F401
except Exception:
    pass
try:
    import _crypt  # noqa: F401
except Exception:
    pass
try:
    import _csv  # noqa: F401
except Exception:
    pass
try:
    import _ctypes  # noqa: F401
except Exception:
    pass
try:
    import _curses  # noqa: F401
except Exception:
    pass
try:
    import _curses_panel  # noqa: F401
except Exception:
    pass
try:
    import _datetime  # noqa: F401
except Exception:
    pass
try:
    import _dbm  # noqa: F401
except Exception:
    pass
try:
    import _decimal  # noqa: F401
except Exception:
    pass
try:
    import _elementtree  # noqa: F401
except Exception:
    pass
try:
    import _frozen_importlib  # noqa: F401
except Exception:
    pass
try:
    import _frozen_importlib_external  # noqa: F401
except Exception:
    pass
try:
    import _functools  # noqa: F401
except Exception:
    pass
try:
    import _gdbm  # noqa: F401
except Exception:
    pass
try:
    import _hashlib  # noqa: F401
except Exception:
    pass
try:
    import _heapq  # noqa: F401
except Exception:
    pass
try:
    import _imp  # noqa: F401
except Exception:
    pass
try:
    import _io  # noqa: F401
except Exception:
    pass
try:
    import _json  # noqa: F401
except Exception:
    pass
try:
    import _locale  # noqa: F401
except Exception:
    pass
try:
    import _lsprof  # noqa: F401
except Exception:
    pass
try:
    import _lzma  # noqa: F401
except Exception:
    pass
try:
    import _markupbase  # noqa: F401
except Exception:
    pass
try:
    import _md5  # noqa: F401
except Exception:
    pass
try:
    import _msi  # noqa: F401
except Exception:
    pass
try:
    import _multibytecodec  # noqa: F401
except Exception:
    pass
try:
    import _multiprocessing  # noqa: F401
except Exception:
    pass
try:
    import _opcode  # noqa: F401
except Exception:
    pass
try:
    import _operator  # noqa: F401
except Exception:
    pass
try:
    import _osx_support  # noqa: F401
except Exception:
    pass
try:
    import _overlapped  # noqa: F401
except Exception:
    pass
try:
    import _pickle  # noqa: F401
except Exception:
    pass
try:
    import _posixshmem  # noqa: F401
except Exception:
    pass
try:
    import _posixsubprocess  # noqa: F401
except Exception:
    pass
try:
    import _py_abc  # noqa: F401
except Exception:
    pass
try:
    import _pydatetime  # noqa: F401
except Exception:
    pass
try:
    import _pydecimal  # noqa: F401
except Exception:
    pass
try:
    import _pyio  # noqa: F401
except Exception:
    pass
try:
    import _pylong  # noqa: F401
except Exception:
    pass
try:
    import _queue  # noqa: F401
except Exception:
    pass
try:
    import _random  # noqa: F401
except Exception:
    pass
try:
    import _scproxy  # noqa: F401
except Exception:
    pass
try:
    import _sha1  # noqa: F401
except Exception:
    pass
try:
    import _sha2  # noqa: F401
except Exception:
    pass
try:
    import _sha3  # noqa: F401
except Exception:
    pass
try:
    import _signal  # noqa: F401
except Exception:
    pass
try:
    import _sitebuiltins  # noqa: F401
except Exception:
    pass
try:
    import _socket  # noqa: F401
except Exception:
    pass
try:
    import _sqlite3  # noqa: F401
except Exception:
    pass
try:
    import _sre  # noqa: F401
except Exception:
    pass
try:
    import _ssl  # noqa: F401
except Exception:
    pass
try:
    import _stat  # noqa: F401
except Exception:
    pass
try:
    import _statistics  # noqa: F401
except Exception:
    pass
try:
    import _string  # noqa: F401
except Exception:
    pass
try:
    import _strptime  # noqa: F401
except Exception:
    pass
try:
    import _struct  # noqa: F401
except Exception:
    pass
try:
    import _symtable  # noqa: F401
except Exception:
    pass
try:
    import _thread  # noqa: F401
except Exception:
    pass
try:
    import _threading_local  # noqa: F401
except Exception:
    pass
try:
    import _tkinter  # noqa: F401
except Exception:
    pass
try:
    import _tokenize  # noqa: F401
except Exception:
    pass
try:
    import _tracemalloc  # noqa: F401
except Exception:
    pass
try:
    import _typing  # noqa: F401
except Exception:
    pass
try:
    import _uuid  # noqa: F401
except Exception:
    pass
try:
    import _warnings  # noqa: F401
except Exception:
    pass
try:
    import _weakref  # noqa: F401
except Exception:
    pass
try:
    import _weakrefset  # noqa: F401
except Exception:
    pass
try:
    import _winapi  # noqa: F401
except Exception:
    pass
try:
    import _zoneinfo  # noqa: F401
except Exception:
    pass
try:
    import abc  # noqa: F401
except Exception:
    pass
try:
    import aifc  # noqa: F401
except Exception:
    pass
try:
    import argparse  # noqa: F401
except Exception:
    pass
try:
    import array  # noqa: F401
except Exception:
    pass
try:
    import ast  # noqa: F401
except Exception:
    pass
try:
    import asyncio  # noqa: F401
except Exception:
    pass
try:
    import atexit  # noqa: F401
except Exception:
    pass
try:
    import audioop  # noqa: F401
except Exception:
    pass
try:
    import base64  # noqa: F401
except Exception:
    pass
try:
    import bdb  # noqa: F401
except Exception:
    pass
try:
    import binascii  # noqa: F401
except Exception:
    pass
try:
    import bisect  # noqa: F401
except Exception:
    pass
try:
    import builtins  # noqa: F401
except Exception:
    pass
try:
    import bz2  # noqa: F401
except Exception:
    pass
try:
    import cProfile  # noqa: F401
except Exception:
    pass
try:
    import calendar  # noqa: F401
except Exception:
    pass
try:
    import cgi  # noqa: F401
except Exception:
    pass
try:
    import cgitb  # noqa: F401
except Exception:
    pass
try:
    import chunk  # noqa: F401
except Exception:
    pass
try:
    import cmath  # noqa: F401
except Exception:
    pass
try:
    import cmd  # noqa: F401
except Exception:
    pass
try:
    import code  # noqa: F401
except Exception:
    pass
try:
    import codecs  # noqa: F401
except Exception:
    pass
try:
    import codeop  # noqa: F401
except Exception:
    pass
try:
    import collections  # noqa: F401
except Exception:
    pass
try:
    import colorsys  # noqa: F401
except Exception:
    pass
try:
    import compileall  # noqa: F401
except Exception:
    pass
try:
    import concurrent  # noqa: F401
except Exception:
    pass
try:
    import configparser  # noqa: F401
except Exception:
    pass
try:
    import contextlib  # noqa: F401
except Exception:
    pass
try:
    import contextvars  # noqa: F401
except Exception:
    pass
try:
    import copy  # noqa: F401
except Exception:
    pass
try:
    import copyreg  # noqa: F401
except Exception:
    pass
try:
    import crypt  # noqa: F401
except Exception:
    pass
try:
    import csv  # noqa: F401
except Exception:
    pass
try:
    import ctypes  # noqa: F401
except Exception:
    pass
try:
    import curses  # noqa: F401
except Exception:
    pass
try:
    import dataclasses  # noqa: F401
except Exception:
    pass
try:
    import datetime  # noqa: F401
except Exception:
    pass
try:
    import dbm  # noqa: F401
except Exception:
    pass
try:
    import decimal  # noqa: F401
except Exception:
    pass
try:
    import difflib  # noqa: F401
except Exception:
    pass
try:
    import dis  # noqa: F401
except Exception:
    pass
try:
    import doctest  # noqa: F401
except Exception:
    pass
try:
    import email  # noqa: F401
except Exception:
    pass
try:
    import encodings  # noqa: F401
except Exception:
    pass
try:
    import ensurepip  # noqa: F401
except Exception:
    pass
try:
    import enum  # noqa: F401
except Exception:
    pass
try:
    import errno  # noqa: F401
except Exception:
    pass
try:
    import faulthandler  # noqa: F401
except Exception:
    pass
try:
    import fcntl  # noqa: F401
except Exception:
    pass
try:
    import filecmp  # noqa: F401
except Exception:
    pass
try:
    import fileinput  # noqa: F401
except Exception:
    pass
try:
    import fnmatch  # noqa: F401
except Exception:
    pass
try:
    import fractions  # noqa: F401
except Exception:
    pass
try:
    import ftplib  # noqa: F401
except Exception:
    pass
try:
    import functools  # noqa: F401
except Exception:
    pass
try:
    import gc  # noqa: F401
except Exception:
    pass
try:
    import genericpath  # noqa: F401
except Exception:
    pass
try:
    import getopt  # noqa: F401
except Exception:
    pass
try:
    import getpass  # noqa: F401
except Exception:
    pass
try:
    import gettext  # noqa: F401
except Exception:
    pass
try:
    import glob  # noqa: F401
except Exception:
    pass
try:
    import graphlib  # noqa: F401
except Exception:
    pass
try:
    import grp  # noqa: F401
except Exception:
    pass
try:
    import gzip  # noqa: F401
except Exception:
    pass
try:
    import hashlib  # noqa: F401
except Exception:
    pass
try:
    import heapq  # noqa: F401
except Exception:
    pass
try:
    import hmac  # noqa: F401
except Exception:
    pass
try:
    import html  # noqa: F401
except Exception:
    pass
try:
    import http  # noqa: F401
except Exception:
    pass
try:
    import imaplib  # noqa: F401
except Exception:
    pass
try:
    import imghdr  # noqa: F401
except Exception:
    pass
try:
    import importlib  # noqa: F401
except Exception:
    pass
try:
    import inspect  # noqa: F401
except Exception:
    pass
try:
    import io  # noqa: F401
except Exception:
    pass
try:
    import ipaddress  # noqa: F401
except Exception:
    pass
try:
    import itertools  # noqa: F401
except Exception:
    pass
try:
    import json  # noqa: F401
except Exception:
    pass
try:
    import keyword  # noqa: F401
except Exception:
    pass
try:
    import lib2to3  # noqa: F401
except Exception:
    pass
try:
    import linecache  # noqa: F401
except Exception:
    pass
try:
    import locale  # noqa: F401
except Exception:
    pass
try:
    import logging  # noqa: F401
except Exception:
    pass
try:
    import lzma  # noqa: F401
except Exception:
    pass
try:
    import mailbox  # noqa: F401
except Exception:
    pass
try:
    import mailcap  # noqa: F401
except Exception:
    pass
try:
    import marshal  # noqa: F401
except Exception:
    pass
try:
    import math  # noqa: F401
except Exception:
    pass
try:
    import mimetypes  # noqa: F401
except Exception:
    pass
try:
    import mmap  # noqa: F401
except Exception:
    pass
try:
    import modulefinder  # noqa: F401
except Exception:
    pass
try:
    import msilib  # noqa: F401
except Exception:
    pass
try:
    import msvcrt  # noqa: F401
except Exception:
    pass
try:
    import multiprocessing  # noqa: F401
except Exception:
    pass
try:
    import netrc  # noqa: F401
except Exception:
    pass
try:
    import nis  # noqa: F401
except Exception:
    pass
try:
    import nntplib  # noqa: F401
except Exception:
    pass
try:
    import nt  # noqa: F401
except Exception:
    pass
try:
    import ntpath  # noqa: F401
except Exception:
    pass
try:
    import nturl2path  # noqa: F401
except Exception:
    pass
try:
    import numbers  # noqa: F401
except Exception:
    pass
try:
    import opcode  # noqa: F401
except Exception:
    pass
try:
    import operator  # noqa: F401
except Exception:
    pass
try:
    import optparse  # noqa: F401
except Exception:
    pass
try:
    import os  # noqa: F401
except Exception:
    pass
try:
    import ossaudiodev  # noqa: F401
except Exception:
    pass
try:
    import pathlib  # noqa: F401
except Exception:
    pass
try:
    import pdb  # noqa: F401
except Exception:
    pass
try:
    import pickle  # noqa: F401
except Exception:
    pass
try:
    import pickletools  # noqa: F401
except Exception:
    pass
try:
    import pipes  # noqa: F401
except Exception:
    pass
try:
    import pkgutil  # noqa: F401
except Exception:
    pass
try:
    import platform  # noqa: F401
except Exception:
    pass
try:
    import plistlib  # noqa: F401
except Exception:
    pass
try:
    import poplib  # noqa: F401
except Exception:
    pass
try:
    import posix  # noqa: F401
except Exception:
    pass
try:
    import posixpath  # noqa: F401
except Exception:
    pass
try:
    import pprint  # noqa: F401
except Exception:
    pass
try:
    import profile  # noqa: F401
except Exception:
    pass
try:
    import pstats  # noqa: F401
except Exception:
    pass
try:
    import pty  # noqa: F401
except Exception:
    pass
try:
    import pwd  # noqa: F401
except Exception:
    pass
try:
    import py_compile  # noqa: F401
except Exception:
    pass
try:
    import pyclbr  # noqa: F401
except Exception:
    pass
try:
    import pydoc  # noqa: F401
except Exception:
    pass
try:
    import pydoc_data  # noqa: F401
except Exception:
    pass
try:
    import pyexpat  # noqa: F401
except Exception:
    pass
try:
    import queue  # noqa: F401
except Exception:
    pass
try:
    import quopri  # noqa: F401
except Exception:
    pass
try:
    import random  # noqa: F401
except Exception:
    pass
try:
    import re  # noqa: F401
except Exception:
    pass
try:
    import readline  # noqa: F401
except Exception:
    pass
try:
    import reprlib  # noqa: F401
except Exception:
    pass
try:
    import resource  # noqa: F401
except Exception:
    pass
try:
    import rlcompleter  # noqa: F401
except Exception:
    pass
try:
    import runpy  # noqa: F401
except Exception:
    pass
try:
    import sched  # noqa: F401
except Exception:
    pass
try:
    import secrets  # noqa: F401
except Exception:
    pass
try:
    import select  # noqa: F401
except Exception:
    pass
try:
    import selectors  # noqa: F401
except Exception:
    pass
try:
    import shelve  # noqa: F401
except Exception:
    pass
try:
    import shlex  # noqa: F401
except Exception:
    pass
try:
    import shutil  # noqa: F401
except Exception:
    pass
try:
    import signal  # noqa: F401
except Exception:
    pass
try:
    import site  # noqa: F401
except Exception:
    pass
try:
    import smtplib  # noqa: F401
except Exception:
    pass
try:
    import sndhdr  # noqa: F401
except Exception:
    pass
try:
    import socket  # noqa: F401
except Exception:
    pass
try:
    import socketserver  # noqa: F401
except Exception:
    pass
try:
    import spwd  # noqa: F401
except Exception:
    pass
try:
    import sqlite3  # noqa: F401
except Exception:
    pass
try:
    import sre_compile  # noqa: F401
except Exception:
    pass
try:
    import sre_constants  # noqa: F401
except Exception:
    pass
try:
    import sre_parse  # noqa: F401
except Exception:
    pass
try:
    import ssl  # noqa: F401
except Exception:
    pass
try:
    import stat  # noqa: F401
except Exception:
    pass
try:
    import statistics  # noqa: F401
except Exception:
    pass
try:
    import string  # noqa: F401
except Exception:
    pass
try:
    import stringprep  # noqa: F401
except Exception:
    pass
try:
    import struct  # noqa: F401
except Exception:
    pass
try:
    import subprocess  # noqa: F401
except Exception:
    pass
try:
    import sunau  # noqa: F401
except Exception:
    pass
try:
    import symtable  # noqa: F401
except Exception:
    pass
try:
    import sys  # noqa: F401
except Exception:
    pass
try:
    import sysconfig  # noqa: F401
except Exception:
    pass
try:
    import syslog  # noqa: F401
except Exception:
    pass
try:
    import tabnanny  # noqa: F401
except Exception:
    pass
try:
    import tarfile  # noqa: F401
except Exception:
    pass
try:
    import telnetlib  # noqa: F401
except Exception:
    pass
try:
    import tempfile  # noqa: F401
except Exception:
    pass
try:
    import termios  # noqa: F401
except Exception:
    pass
try:
    import textwrap  # noqa: F401
except Exception:
    pass
try:
    import threading  # noqa: F401
except Exception:
    pass
try:
    import time  # noqa: F401
except Exception:
    pass
try:
    import timeit  # noqa: F401
except Exception:
    pass
try:
    import token  # noqa: F401
except Exception:
    pass
try:
    import tokenize  # noqa: F401
except Exception:
    pass
try:
    import tomllib  # noqa: F401
except Exception:
    pass
try:
    import trace  # noqa: F401
except Exception:
    pass
try:
    import traceback  # noqa: F401
except Exception:
    pass
try:
    import tracemalloc  # noqa: F401
except Exception:
    pass
try:
    import tty  # noqa: F401
except Exception:
    pass
try:
    import turtle  # noqa: F401
except Exception:
    pass
try:
    import types  # noqa: F401
except Exception:
    pass
try:
    import typing  # noqa: F401
except Exception:
    pass
try:
    import unicodedata  # noqa: F401
except Exception:
    pass
try:
    import unittest  # noqa: F401
except Exception:
    pass
try:
    import urllib  # noqa: F401
except Exception:
    pass
try:
    import uu  # noqa: F401
except Exception:
    pass
try:
    import uuid  # noqa: F401
except Exception:
    pass
try:
    import venv  # noqa: F401
except Exception:
    pass
try:
    import warnings  # noqa: F401
except Exception:
    pass
try:
    import wave  # noqa: F401
except Exception:
    pass
try:
    import weakref  # noqa: F401
except Exception:
    pass
try:
    import webbrowser  # noqa: F401
except Exception:
    pass
try:
    import winreg  # noqa: F401
except Exception:
    pass
try:
    import winsound  # noqa: F401
except Exception:
    pass
try:
    import wsgiref  # noqa: F401
except Exception:
    pass
try:
    import xdrlib  # noqa: F401
except Exception:
    pass
try:
    import xml  # noqa: F401
except Exception:
    pass
try:
    import xmlrpc  # noqa: F401
except Exception:
    pass
try:
    import zipapp  # noqa: F401
except Exception:
    pass
try:
    import zipfile  # noqa: F401
except Exception:
    pass
try:
    import zipimport  # noqa: F401
except Exception:
    pass
try:
    import zlib  # noqa: F401
except Exception:
    pass
try:
    import zoneinfo  # noqa: F401
except Exception:
    pass
