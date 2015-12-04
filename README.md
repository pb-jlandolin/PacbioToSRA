#PacbioToSRA
This repo contains scripts, instructions, and examples on preparing PacBio sequence data for data submission to the SRA. 

## Instructions
1. Register project and samples
2. Setup script's environment
3. Run the script
4. Update spreadsheet and email it to NCBI


##Step 1. Register project and samples

    Go to https://submit.ncbi.nlm.nih.gov/ and register your Bioproject
    Go to https://submit.ncbi.nlm.nih.gov/ and register your Biosample


##Step 2. Prepare script's environment
####Install Aspera software:

*  Download and install the linux version of aspera-connect that contains the ascp command (http://downloads.asperasoft.com/en/downloads/50)
*  Add Aspera's ascp command to your $PATH. Example:  
```
    $ export PATH='/path/to/ascp/command':$PATH
```
*  Verify that the Aspera's ascp is in your $PATH.  
```
    $ which ascp
```

####Setup virtual environment:

```
$ virtualenv virtualenv_PacbioToSRA
$ source virtualenv_PacbioToSRA/bin/activate
$ pip install -r requirements.txt
```


##Step 3. Run the script
####Usage:
```
$ bin/send_to_ncbi.py --bioproject_accession=BIOPROJECT_ACCESSION --biosample_accession=BIOSAMPLE_ACCESSION --input_fofn=INPUT_FOFN_FILE --ncbi_username=NCBI_USERNAME --ncbi_ssh_key_file=SSH_KEY_FILE [--excel_output_file=EXCEL_OUTPUT_FILE]
```
*  NCBI_USERNAME is the username that ncbi assigns to your institution (get this from info@ncbi.nlm.nih.gov)
*  SSH _KEY _FILE is the ssh key file that you generated for your institution (http://www.ncbi.nlm.nih.gov/books/NBK180157/)
*  For additional help on the script, type:  
```$ bin/send_to_ncbi.py --help```


####Example:
```
$ bin/send_to_ncbi.py --bioproject_accession=project1 --biosample_accession=sample1 --input_fofn=/path/to/input.fofn --ncbi_username=myusername --ncbi_ssh_key_file=/path/to/ssh/file --excel_output_file=my_sra_submission.xls
```

##Step 4. Update spreadsheet and email it to NCBI
*  Update the spreadsheet with any additional information.
*  Email (info@ncbi.nlm.nih.gov) and attach the spreadsheet to the email. 

