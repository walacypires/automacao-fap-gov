$BRAVE   = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
$USERDATA = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data"
$PROFILE  = "Pessoal"   # ajuste se seu perfil chamar "Default" ou outro

$HOSTRULES = 'MAP sso.acesso.gov.br 161.148.168.40,MAP fap.dataprev.gov.br 200.152.35.17,EXCLUDE localhost'
$AUTOSEL   = '[{"pattern":"https://sso.acesso.gov.br","filter":{"ISSUER":{"CN":"AC SOLUTI Multipla v5"}}},{"pattern":"https://fap.dataprev.gov.br","filter":{"ISSUER":{"CN":"AC SOLUTI Multipla v5"}}}]'

& $BRAVE `
  --remote-debugging-port=9222 `
  --user-data-dir="$USERDATA" `
  --profile-directory="$PROFILE" `
  --host-resolver-rules="$HOSTRULES" `
  --auto-select-certificate-for-urls="$AUTOSEL" `
  --start-maximized
