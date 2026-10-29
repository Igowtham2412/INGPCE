# Demo employee photos

Drop enrollment photos for the demo employees here, one folder per person:

```
demo/employees/EMP001_Ravi_Kumar/*.jpg

demo/employees/EMP002_Anita_Sharma/*.jpg

demo/employees/EMP003_John_Mathew/*.jpg

demo/employees/EMP004_Priya_Nair/*.jpg

demo/employees/EMP005_Imran_Khan/*.jpg
```

* 3-10 photos per person; front-facing, one face per photo, good lighting.
* No photos? `python demo/seed_employees.py --lfw` downloads one distinct LFW
  person per employee folder (scikit-learn, ~200 MB first run).
* After adding photos run `python demo/seed_employees.py` to rebuild
  `demo/seed_employees.json` (with real ArcFace embeddings when run on the
  on-prem box).
* The SIMULATION demo (`--source simulate`) does not use these photos — it
  uses synthetic identities — but real-mode (`--source <file|webcam|rtsp://...>`)
  matches against the embeddings computed from them.

**Privacy:** these folders are for DEMO data only. Real employee photos belong
in the app's governed enrollment flow (dashboard → Face Enrollment), where
embeddings are encrypted and access is role-restricted.
