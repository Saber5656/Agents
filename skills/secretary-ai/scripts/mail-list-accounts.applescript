-- secretary-ai: Apple Mail のアカウント一覧
-- Usage: osascript scripts/mail-list-accounts.applescript
-- Output: 1行1アカウント (name<TAB>type<TAB>email_addresses)

tell application "Mail"
	set output to ""
	repeat with acc in every account
		try
			set accName to name of acc as string
			set accType to (account type of acc) as string
			set accEmails to ""
			try
				set emailList to email addresses of acc
				if emailList is not missing value then
					set AppleScript's text item delimiters to ","
					set accEmails to emailList as string
					set AppleScript's text item delimiters to ""
				end if
			end try
			set output to output & accName & tab & accType & tab & accEmails & linefeed
		end try
	end repeat
	return output
end tell
