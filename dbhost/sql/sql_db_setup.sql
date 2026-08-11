-- ── ТАБЛИЦЫ ──────────────────────────────────────────────────

CREATE TABLE users (
    user_id     INT PRIMARY KEY,
    email       VARCHAR(100),
    country     VARCHAR(10),
    created_at  TIMESTAMP
);

CREATE TABLE providers (
    provider_id   INT PRIMARY KEY,
    provider_name VARCHAR(100),
    contact_email VARCHAR(100)
);

CREATE TABLE transactions (
    transaction_id INT PRIMARY KEY,
    user_id        INT,
    amount         DECIMAL(10,2),
    status         VARCHAR(20),   -- success / failed / pending / declined / refunded
    type           VARCHAR(20),   -- deposit / withdrawal
    provider_id    INT,
    error_code     VARCHAR(50),
    created_at     TIMESTAMP
);

CREATE TABLE sessions (
    session_id  INT PRIMARY KEY,
    user_id     INT,
    game_id     INT,
    started_at  TIMESTAMP,
    ended_at    TIMESTAMP
);

CREATE TABLE games (
    game_id     INT PRIMARY KEY,
    game_name   VARCHAR(100),
    provider_id INT
);

CREATE TABLE provider_responses (
    response_id      INT PRIMARY KEY,
    transaction_id   INT,
    external_status  VARCHAR(20),   -- статус на стороне провайдера
    responded_at     TIMESTAMP
);

-- ── ПРОВАЙДЕРЫ ───────────────────────────────────────────────

INSERT INTO providers VALUES
(1, 'PayCore',    'support@paycore.io'),
(2, 'FastPay',    'ops@fastpay.com'),
(3, 'CryptoGate', 'help@cryptogate.net');

-- ── ИГРЫ ─────────────────────────────────────────────────────

INSERT INTO games VALUES
(1,  'Lucky Slots',   1),
(2,  'Poker Stars',   1),
(3,  'Roulette Live', 2),
(4,  'Blackjack Pro', 2),
(5,  'Dice King',     3); 

-- ── ПОЛЬЗОВАТЕЛИ ─────────────────────────────────────────────

INSERT INTO users VALUES
(1,  'ivan@mail.ru',      'RU', NOW() - INTERVAL '90 days'),
(2,  'anna@mail.ru',      'RU', NOW() - INTERVAL '60 days'),
(3,  'john@gmail.com',    'US', NOW() - INTERVAL '45 days'),
(4,  'petr@yandex.ru',    'RU', NOW() - INTERVAL '10 days'),  -- новый
(5,  'maria@mail.ru',     'RU', NOW() - INTERVAL '5 days'),   -- новый
(6,  'alex@gmail.com',    'US', NOW() - INTERVAL '3 days'),   -- новый
(7,  'ghost@example.com', 'DE', NOW() - INTERVAL '20 days');  -- нет транзакций → для LEFT JOIN

-- ── ТРАНЗАКЦИИ ───────────────────────────────────────────────

INSERT INTO transactions VALUES
-- Успешные депозиты (разные пользователи и провайдеры)
(100, 1, 500.00,   'success',  'deposit',    1, NULL,            NOW() - INTERVAL '5 days'),
(101, 2, 200.00,   'failed',   'deposit',    2, 'INSUFFICIENT',  NOW() - INTERVAL '4 days'),
(102, 1, 1000.00,  'success',  'withdrawal', 1, NULL,            NOW() - INTERVAL '3 days'),
(103, 2, 300.00,   'pending',  'deposit',    1, NULL,            NOW() - INTERVAL '3 hours'),  -- pending > 2ч
(104, 3, 750.00,   'success',  'deposit',    2, NULL,            NOW() - INTERVAL '2 days'),
(105, 4, 1500.00,  'success',  'deposit',    1, NULL,            NOW() - INTERVAL '1 day'),
(106, 5, 250.00,   'declined', 'deposit',    2, 'DO_NOT_HONOUR', NOW() - INTERVAL '1 day'),
(107, 1, 800.00,   'success',  'deposit',    3, NULL,            NOW() - INTERVAL '12 hours'),
(108, 2, 150.00,   'failed',   'withdrawal', 2, 'LIMIT_EXCEED',  NOW() - INTERVAL '10 hours'),
(109, 3, 2000.00,  'success',  'withdrawal', 1, NULL,            NOW() - INTERVAL '6 hours'),

-- Сегодняшние транзакции (для фильтров CURRENT_DATE)
(110, 1, 400.00,   'success',  'deposit',    1, NULL,            NOW() - INTERVAL '3 hours'),
(111, 4, 600.00,   'failed',   'deposit',    2, 'TIMEOUT',       NOW() - INTERVAL '2 hours'),
(112, 5, 900.00,   'success',  'deposit',    1, NULL,            NOW() - INTERVAL '1 hour'),
(113, 6, 3000.00,  'success',  'deposit',    3, NULL,            NOW() - INTERVAL '30 minutes'),
(114, 6, 2900.00,  'success',  'withdrawal', 3, NULL,            NOW() - INTERVAL '20 minutes'), -- AML: депозит и быстрый вывод

-- Pending зависшие > 2 часов (для диагностического запроса)
(115, 2, 500.00,   'pending',  'deposit',    2, NULL,            NOW() - INTERVAL '4 hours'),
(116, 3, 350.00,   'pending',  'withdrawal', 1, NULL,            NOW() - INTERVAL '3 hours'),

-- Дубли: одинаковые user_id + amount + provider_id в течение 5 минут
(117, 1, 100.00,   'success',  'deposit',    1, NULL,            NOW() - INTERVAL '1 day' - INTERVAL '2 minutes'),
(118, 1, 100.00,   'success',  'deposit',    1, NULL,            NOW() - INTERVAL '1 day'),  -- дубль 117

-- Крупные транзакции для топа
(119, 3, 5000.00,  'success',  'deposit',    1, NULL,            NOW() - INTERVAL '2 days'),
(120, 4, 4500.00,  'success',  'deposit',    2, NULL,            NOW() - INTERVAL '2 days'),
(121, 5, 3800.00,  'success',  'deposit',    1, NULL,            NOW() - INTERVAL '3 days'),

-- Много failed у провайдера 2 (для HAVING > 100 — добавим пачку)
(200, 1, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '1 day'),
(201, 2, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '1 day'),
(202, 3, 50.00,  'failed', 'deposit', 2, 'INSUFFICIENT',  NOW() - INTERVAL '1 day'),
(203, 4, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '1 day'),
(204, 5, 50.00,  'failed', 'deposit', 2, 'DO_NOT_HONOUR', NOW() - INTERVAL '1 day'),
(205, 1, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '2 days'),
(206, 2, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '2 days'),
(207, 3, 50.00,  'failed', 'deposit', 2, 'INSUFFICIENT',  NOW() - INTERVAL '2 days'),
(208, 4, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '2 days'),
(209, 5, 50.00,  'failed', 'deposit', 2, 'TIMEOUT',       NOW() - INTERVAL '2 days');

-- ── PROVIDER RESPONSES (расхождение статусов) ─────────────────
-- tx 110: у нас success, у провайдера — failed
-- остальные совпадают

INSERT INTO provider_responses VALUES
(1, 100, 'success', NOW() - INTERVAL '5 days'),
(2, 101, 'failed',  NOW() - INTERVAL '4 days'),
(3, 102, 'success', NOW() - INTERVAL '3 days'),
(4, 104, 'success', NOW() - INTERVAL '2 days'),
(5, 105, 'success', NOW() - INTERVAL '1 day'),
(6, 107, 'success', NOW() - INTERVAL '12 hours'),
(7, 109, 'success', NOW() - INTERVAL '6 hours'),
(8, 110, 'failed',  NOW() - INTERVAL '3 hours'),  -- ← расхождение
(9, 112, 'success', NOW() - INTERVAL '1 hour'),
(10,113, 'success', NOW() - INTERVAL '30 minutes');

-- ── СЕССИИ (игровая активность) ──────────────────────────────
-- user 6 НЕ имеет сессий между своим депозитом (113) и выводом (114) → AML-флаг
-- game 5 (Dice King) не имеет сессий вообще

INSERT INTO sessions VALUES
(1,  1, 1, NOW() - INTERVAL '5 days',              NOW() - INTERVAL '5 days' + INTERVAL '2 hours'),
(2,  1, 2, NOW() - INTERVAL '4 days',              NOW() - INTERVAL '4 days' + INTERVAL '1 hour'),
(3,  2, 3, NOW() - INTERVAL '4 days',              NOW() - INTERVAL '4 days' + INTERVAL '30 minutes'),
(4,  3, 1, NOW() - INTERVAL '3 days',              NOW() - INTERVAL '3 days' + INTERVAL '3 hours'),
(5,  4, 4, NOW() - INTERVAL '2 days',              NOW() - INTERVAL '2 days' + INTERVAL '1 hour'),
(6,  5, 2, NOW() - INTERVAL '1 day',               NOW() - INTERVAL '1 day'  + INTERVAL '2 hours'),
(7,  1, 3, NOW() - INTERVAL '12 hours',            NOW() - INTERVAL '10 hours'),
(8,  3, 1, NOW() - INTERVAL '6 hours',             NOW() - INTERVAL '5 hours'),
(9,  1, 1, NOW() - INTERVAL '2 hours' - INTERVAL '30 minutes', NOW() - INTERVAL '2 hours'),
(10, 4, 2, NOW() - INTERVAL '1 hour',              NOW() - INTERVAL '30 minutes');
-- user_id 6 намеренно не имеет сессий (AML-кейс)
