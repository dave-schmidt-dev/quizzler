# ITN 213 Study Guide Generation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autonomously generate a comprehensive study guide for ITN 213 by synthesizing transcripts, notes, case studies, and quizzes into a single Markdown document with source citations and visual references.

**Architecture:** We will process the course materials in chronological batches. For each batch, we will extract key concepts and exact Q&A, formatted with source citations. We will integrate visuals (e.g., `architecture_diagram.png`) where they add value. Finally, we will assemble these sections into `ITN213_Final_Study_Guide.md`.

**Tech Stack:** Bash, Markdown generation, LLM synthesis.

---

### Task 1: Generate Early Course & Midterm Section

**Files:**
- Read: `/Users/dave/Documents/ITN213/Midterm Review Summary.md`, `/Users/dave/Documents/ITN213/Session 5 Notes (Zoom).md`, `/Users/dave/Documents/ITN213/Case Study #1.md`
- Create: `/Users/dave/Documents/ITN213/tmp_part1.md`

- [ ] **Step 1: Write the validation script (Test)**
```bash
cat << 'EOF' > /Users/dave/Documents/ITN213/validate_part1.sh
#!/bin/bash
if ! grep -q "Source:" /Users/dave/Documents/ITN213/tmp_part1.md; then echo "FAIL: No citations found"; exit 1; fi
echo "PASS"
EOF
chmod +x /Users/dave/Documents/ITN213/validate_part1.sh
```

- [ ] **Step 2: Run test to verify it fails**
```bash
/Users/dave/Documents/ITN213/validate_part1.sh
```
Expected: FAIL: No such file or No citations found

- [ ] **Step 3: Extract & Synthesize Content**
Use your context/reading tools to ingest the Midterm Review Summary, Session 5 Notes, and Case Study 1. Generate `tmp_part1.md` with:
1. Concept Summaries (bullet points).
2. Q&A (flashcard style).
3. **Mandatory**: Every single section and question MUST have a `[Source: <filename>]` citation.

- [ ] **Step 4: Run test to verify it passes**
```bash
/Users/dave/Documents/ITN213/validate_part1.sh
```
Expected: PASS

### Task 2: Generate Late Course & Case Study 2 Section

**Files:**
- Read: `/Users/dave/Documents/ITN213/module9_knowledge_check.md`, `/Users/dave/Documents/ITN213/module_10_study.md`, `/Users/dave/Documents/ITN213/quiz_module11.md`, `/Users/dave/Documents/ITN213/case_study_2_instagram.md`
- Create: `/Users/dave/Documents/ITN213/tmp_part2.md`

- [ ] **Step 1: Write the validation script**
```bash
cat << 'EOF' > /Users/dave/Documents/ITN213/validate_part2.sh
#!/bin/bash
if ! grep -q "Source:" /Users/dave/Documents/ITN213/tmp_part2.md; then echo "FAIL: No citations found"; exit 1; fi
echo "PASS"
EOF
chmod +x /Users/dave/Documents/ITN213/validate_part2.sh
```

- [ ] **Step 2: Synthesize Content**
Use your tools to read the Module 9-11 files and Case Study 2. Generate `tmp_part2.md` with Concept Summaries and Q&A. Ensure you extract general concepts from Case Study 2, not trivia. 
**Mandatory**: Every single section and question MUST have a `[Source: <filename>]` citation. Include a visual reference to `architecture_diagram.png` if it applies to Module 10 or 11.

- [ ] **Step 3: Run test to verify it passes**
```bash
/Users/dave/Documents/ITN213/validate_part2.sh
```
Expected: PASS

### Task 3: Assemble Final Guide and Clean Up

**Files:**
- Read: `/Users/dave/Documents/ITN213/tmp_part1.md`, `/Users/dave/Documents/ITN213/tmp_part2.md`
- Create: `/Users/dave/Documents/ITN213/ITN213_Final_Study_Guide.md`

- [ ] **Step 1: Concatenate and format the final guide**
```bash
cat << 'EOF' > /Users/dave/Documents/ITN213/ITN213_Final_Study_Guide.md
# ITN 213 Final Study Guide

EOF
cat /Users/dave/Documents/ITN213/tmp_part1.md >> /Users/dave/Documents/ITN213/ITN213_Final_Study_Guide.md
cat /Users/dave/Documents/ITN213/tmp_part2.md >> /Users/dave/Documents/ITN213/ITN213_Final_Study_Guide.md
```

- [ ] **Step 2: Final Verification**
```bash
grep -q "Source:" /Users/dave/Documents/ITN213/ITN213_Final_Study_Guide.md && echo "PASS"
```
Expected: PASS

- [ ] **Step 3: Clean up temporary files**
```bash
rm /Users/dave/Documents/ITN213/tmp_part*.md /Users/dave/Documents/ITN213/validate_part*.sh
```
