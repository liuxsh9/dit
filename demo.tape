# Dit Demo — Git-like version control for LLM training data
Output docs/images/demo.gif
Set Shell "bash"
Set FontSize 15
Set Width 960
Set Height 540
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set PlaybackSpeed 0.5
Set Framerate 24
Set CursorBlink false

# Hidden setup: PATH + temp dir + dit init
Hide
Type `export PATH="/Users/lxs/code/datahub/.venv/bin:$PATH" && export PS1="$ " && cd $(mktemp -d) && dit init > /dev/null 2>&1 && clear` Enter
Sleep 2s
Show

# Create training data
Type `cat > train.jsonl << 'EOF'` Enter
Sleep 0.2s
Type `{"messages":[{"role":"user","content":"What is LRU cache?"},{"role":"assistant","content":"LRU cache evicts least recently used items when full."}]}` Enter
Sleep 0.2s
Type `{"messages":[{"role":"user","content":"Explain quicksort"},{"role":"assistant","content":"Quicksort is a divide-and-conquer sorting algorithm."}]}` Enter
Sleep 0.2s
Type `EOF` Enter
Sleep 0.5s

# Stage and commit
Type "dit add train.jsonl" Enter
Sleep 1s
Type `dit commit -m "initial training data"` Enter
Sleep 1.5s

# Status
Type "dit status" Enter
Sleep 1.5s

# Branch
Type "dit branch improve-answers && dit checkout improve-answers" Enter
Sleep 1.5s

# Refresh an answer
Type `cat > train.jsonl << 'EOF'` Enter
Sleep 0.2s
Type `{"messages":[{"role":"user","content":"What is LRU cache?"},{"role":"assistant","content":"LRU evicts least recently used items. Use OrderedDict in Python."}]}` Enter
Sleep 0.2s
Type `{"messages":[{"role":"user","content":"Explain quicksort"},{"role":"assistant","content":"Quicksort is a divide-and-conquer sorting algorithm."}]}` Enter
Sleep 0.2s
Type `EOF` Enter
Sleep 0.5s

# Semantic diff — shows refresh
Type "dit diff" Enter
Sleep 2.5s

# Commit and merge
Type "dit add train.jsonl" Enter
Sleep 0.8s
Type `dit commit -m "improve LRU explanation"` Enter
Sleep 1.5s
Type "dit checkout main" Enter
Sleep 1s
Type "dit merge improve-answers" Enter
Sleep 2s

# Search
Type `dit search "LRU" --field messages.content` Enter
Sleep 2.5s

# Blame
Type "dit blame train.jsonl" Enter
Sleep 2.5s

# Log
Type "dit log --oneline" Enter
Sleep 2.5s

# Dedup
Type "dit dedup" Enter
Sleep 2s

Sleep 3s
