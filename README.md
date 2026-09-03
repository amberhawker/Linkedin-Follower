# Linkedin Follower
by Amber Hawker

Hey! In order to run this program, we just need a few steps from you.

First, make sure python is installed on your system. You can google this one.

Afterwards, you need to configure `.env`, there is an example at `.env.example`, and all that is required is an API key from [Hatz](https://hatz.ai).

You can then put an xlsx file in the `data/` folder, names `unenriched.xlsx`. By default, the program will extract from a table, starting with col "Organization", and exits when it gets to "Data Confidence". You can edit the end col in the Enrich.py file.

You may run into virtual environment issues through this process, but I believe in you and the process of `python3 -m venv venv` && sourcing it

Run `setup.py`, and login to linkedin when it opens the browser window. If there are errors here, first make sure you have Chrome installed.

Then, run `Enrich.py`. The program will first search for all of the names, and then it will one-by-one add them on Linkedin.
If the program crashes, you can simply restart it, and it will pick up from where it left off.

Hope you enjoy! Create an issue or contact me if you need any assistance.