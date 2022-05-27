#!/bin/bash
mkdir lib
cd lib
git clone git@version.aalto.fi:dtap1/real-time-person-detection.git
mv real-time-person-detection rtpd
mkdir ../models
mkdir ../models/pd_retail_13
cp -r rtpd/model/* ../models/pd_retail_13/