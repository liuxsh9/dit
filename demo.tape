# Dit Demo — Git-like version control for LLM training data
Output docs/images/demo.gif
Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 540
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set PlaybackSpeed 0.5
Set Framerate 24
Set CursorBlink false

# Hidden setup: PATH + temp dir + dit init + create initial data
Hide
Type `export PATH="/Users/lxs/code/datahub/.venv/bin:$PATH" && export PS1="$ " && cd $(mktemp -d) && dit init > /dev/null 2>&1` Enter
Sleep 1.5s
Type `cat > train.jsonl << 'EOF'` Enter
Type `{"messages":[{"role":"user","content":"What is LRU cache?"},{"role":"assistant","content":"LRU cache evicts least recently used items when full."}]}` Enter
Type `{"messages":[{"role":"user","content":"Explain quicksort"},{"role":"assistant","content":"Quicksort is a divide-and-conquer sorting algorithm."}]}` Enter
Type `EOF` Enter
Sleep 0.5s
Type `dit add train.jsonl > /dev/null 2>&1 && dit commit -m "initial training data" > /dev/null 2>&1 && clear` Enter
Sleep 1.5s
Show

# Stage and commit
Type "dit status" Enter
Sleep 2s

# Branch
Type "dit branch improve-answers && dit checkout improve-answers" Enter
Sleep 1.5s

# Hidden: refresh an answer
Hide
Type `cat > train.jsonl << 'EOF'` Enter
Type `{"messages":[{"role":"user","content":"What is LRU cache?"},{"role":"assistant","content":"LRU evicts least recently used items. Use OrderedDict in Python."}]}` Enter
Type `{"messages":[{"role":"user","content":"Explain quicksort"},{"role":"assistant","content":"Quicksort is a divide-and-conquer sorting algorithm."}]}` Enter
Type `EOF` Enter
Sleep 0.5s
Show

# Semantic diff — the star feature
Type "dit diff" Enter
Sleep 3s

# Commit and merge
Type "dit add train.jsonl" Enter
Sleep 1s
Type `dit commit -m "improve LRU explanation"` Enter
Sleep 1.5s
Type "dit checkout main" Enter
Sleep 1s
Type "dit merge improve-answers" Enter
Sleep 2.5s

# Hidden: start dit-core server for push demo
Hide
Type `SERVICE_TOKEN=demo-secret DIT_DB_PASSWORD=demo-pass CORE_IMAGE=dit-core:test docker compose -f /Users/lxs/code/datahub/docker-compose.core.yml up -d 2>/dev/null` Enter
Sleep 8s
Type `for i in $(seq 1 20); do curl -sf http://localhost:8000/health && break || sleep 1; done` Enter
Sleep 3s
Type `export ADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/admin/tokens -H "Content-Type: application/json" -H "X-Service-Token: demo-secret" -d '{"label":"demo","permissions":"admin"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")` Enter
Sleep 1s
Type `curl -s -X POST http://localhost:8000/api/v1/repos -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"name":"demo-repo"}' > /dev/null` Enter
Sleep 1s
Type `dit remote add origin http://localhost:8000/demo-repo 2>/dev/null; dit auth set-token $ADMIN_TOKEN --remote origin > /dev/null` Enter
Sleep 1s
Show

# Push to remote server
Type "dit push" Enter
Sleep 3s

# Hidden: cleanup server
Hide
Type `SERVICE_TOKEN=demo-secret DIT_DB_PASSWORD=demo-pass docker compose -f /Users/lxs/code/datahub/docker-compose.core.yml down -v 2>/dev/null` Enter
Sleep 3s
Show

# Search
Type `dit search "LRU" --field messages.content` Enter
Sleep 3s

# Blame
Type "dit blame train.jsonl" Enter
Sleep 3s

# Log
Type "dit log --oneline" Enter
Sleep 3s

# Dedup
Type "dit dedup" Enter
Sleep 3s

Sleep 2s
