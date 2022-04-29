FROM ubuntu:focal

RUN apt -y update 
RUN apt -y install python3

WORKDIR /build

RUN python -m ensurepip
RUN python -m pip install --upgrade pip
RUN python -m pip install pyinstaller
RUN if [ -f requirements.txt ]; then pip install -r requirements.txt; fi