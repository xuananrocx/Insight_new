; ===== Insight Windows 安装包（NSIS Modern UI）=====
; 用法（在仓库根目录执行）：
;     makensis -DAPPVERSION=0.1.1 build/insight.nsi
; 前置：pyinstaller build/insight.spec --noconfirm 已生成 dist/Insight/（onedir）
; 产物：Insight-Windows.exe（安装包）

!include "MUI2.nsh"

; APPVERSION 由 makensis -DAPPVERSION=... 传入，确保与代码版本一致
!ifndef APPVERSION
  !define APPVERSION "0.0.0"
!endif

Name "Insight ${APPVERSION}"
OutFile "Insight-Windows.exe"
Unicode true
InstallDir "$PROGRAMFILES64\Insight"
InstallDirRegKey HKLM "Software\Insight" "InstallDir"
RequestExecutionLevel admin
ShowInstDetails show

; ---- 向导页面 ----
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; ---- 语言 ----
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ====================================================================
; 安装
; ====================================================================
Section "Install" SecInstall
  SectionIn RO   ; 必装，不可取消勾选

  SetOutPath "$INSTDIR"
  ; 递归拷贝 onedir 产物（Insight.exe + _internal/ 等全部依赖）
  File /r "dist\Insight\*.*"

  ; 注册到「控制面板 → 程序与功能」（可卸载）
  WriteRegStr HKLM "Software\Insight" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "DisplayName" "Insight"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "Publisher" "Insight"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; 开始菜单快捷方式
  CreateDirectory "$SMPROGRAMS\Insight"
  CreateShortcut "$SMPROGRAMS\Insight\Insight.lnk" "$INSTDIR\Insight.exe"
  CreateShortcut "$SMPROGRAMS\Insight\卸载 Insight.lnk" "$INSTDIR\uninstall.exe"

  ; 桌面快捷方式
  CreateShortcut "$DESKTOP\Insight.lnk" "$INSTDIR\Insight.exe"
SectionEnd

; ====================================================================
; 卸载
; ====================================================================
Section "Uninstall"
  ; 删除安装目录（含 Insight.exe + _internal/ + uninstall.exe）
  RMDir /r "$INSTDIR"

  ; 删除快捷方式
  RMDir /r "$SMPROGRAMS\Insight"
  Delete "$DESKTOP\Insight.lnk"

  ; 清理注册表
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Insight"
  DeleteRegKey HKLM "Software\Insight"
SectionEnd
