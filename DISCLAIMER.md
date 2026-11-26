# Legal Disclaimer

## Software Provided "AS IS"

AI-BRAIN is an open-source software project provided under the MIT License.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHORS, COPYRIGHT HOLDERS, OR CONTRIBUTORS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Limitation of Liability

Under no circumstances shall the author, maintainers, or contributors be held liable for:
1. Any direct, indirect, incidental, consequential, special, punitive, or exemplary damages arising out of the use or inability to use this software.
2. Loss of data, corruption of files, unauthorized data modification, or unintentional data exposure resulting from misconfiguration, system errors, third-party agent behavior, or local software crashes.
3. Decisions, code, or technical architecture produced or executed by AI agents utilizing this memory vault.

## Critical Data Backup Recommendations

AI-BRAIN interacts with local file systems and writes directly to Markdown files and SQLite database tables. While the software employs safe file operations, concurrency guards, and Write-Ahead Logging (WAL):

- Maintain Independent Backups: Users are strongly advised to maintain regular, independent backups of their notes, vaults, and code repositories (e.g., via Time Machine, Borg, Git, or external cloud backup).
- Never Store Plaintext Credentials: While AI-BRAIN includes automated sanitizers to redact API keys and secrets, users remain solely responsible for the security and confidentiality of sensitive data stored in their local environments.
- Verify Automated Changes: AI coding assistants operating autonomously with memory tools should be periodically reviewed to ensure changes align with intended development goals.
