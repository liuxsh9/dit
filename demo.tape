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
