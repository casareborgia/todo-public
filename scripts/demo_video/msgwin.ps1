param([string]$Sender, [string]$Sent, [string]$BodyFile, [int]$X = 1280, [int]$Y = 120, [switch]$TopMost)
# 시연용 재현 쪽지 창 (EzQ 받은쪽지 창처럼 본문을 내장 IE 화면에 표시)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$body = [IO.File]::ReadAllText($BodyFile, [Text.Encoding]::UTF8)
$f = New-Object Windows.Forms.Form
$f.Text = '받은쪽지 (시연용 재현)'
$f.ClientSize = New-Object Drawing.Size 600, 300
$f.StartPosition = 'Manual'; $f.Location = New-Object Drawing.Point $X, $Y
if ($TopMost) { $f.TopMost = $true }
$f.BackColor = [Drawing.Color]::White
$f.FormBorderStyle = 'FixedSingle'; $f.MaximizeBox = $false
$head = New-Object Windows.Forms.Panel; $head.Dock = 'Top'; $head.Height = 64; $head.BackColor = [Drawing.Color]::FromArgb(241, 245, 249)
$l1 = New-Object Windows.Forms.Label; $l1.Text = "보낸 사람   $Sender"; $l1.Font = New-Object Drawing.Font('Malgun Gothic', 10, [Drawing.FontStyle]::Bold); $l1.Location = New-Object Drawing.Point 16, 10; $l1.AutoSize = $true
$l2 = New-Object Windows.Forms.Label; $l2.Text = "보낸 시각   $Sent"; $l2.Font = New-Object Drawing.Font('Malgun Gothic', 9); $l2.ForeColor = [Drawing.Color]::FromArgb(100, 116, 139); $l2.Location = New-Object Drawing.Point 16, 36; $l2.AutoSize = $true
$head.Controls.Add($l1); $head.Controls.Add($l2)
$b = New-Object Windows.Forms.WebBrowser; $b.Dock = 'Fill'; $b.ScrollBarsEnabled = $false
$f.Controls.Add($b); $f.Controls.Add($head)
$b.DocumentText = "<html><body style='margin:18px;font-family:Malgun Gothic;font-size:15px;color:#0f172a;line-height:1.6;word-break:keep-all'>$body</body></html>"
[void]$f.ShowDialog()
