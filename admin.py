# import bcrypt

# import base64
# print(base64.b64decode("YWRtaW5AZXhhbXBsZS5jb206Zm90b24zMTM=").decode())

import bcrypt

password = "foton313"
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
print(hashed)

'''
UPDATE users
SET password_hash = '$2b$12$Y7wGSCp0fIoeZl9yHHQ9iucpB5ElkB417lJ6CPO3MmjCxYrHeenaO'
WHERE email = 'admin@example.com';
'''



# password = "foton313"  # придумайте свой сильный пароль
# hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
# print(hashed)

# $2b$12$MKTEyH3kZCJF7/Bwb5BZh.HF5GtSYHVKaigZkytSP40PE5enNanUe
'''
INSERT INTO users (id, email, password_hash, display_name, role, is_active, capacity_per_day, created_date, modified_date)
VALUES (
    gen_random_uuid(),
    'admin@example.com',
    '$2b$12$Y7wGSCp0fIoeZl9yHHQ9iucpB5ElkB417lJ6CPO3MmjCxYrHeenaO',
    'Admin',
    'Administrator',
    TRUE,
    8.0,
    NOW(),
    NOW()
);
'''