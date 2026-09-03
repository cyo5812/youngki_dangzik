# 영기팀 당직 일정표

토요일/휴일 3팀(영기·소강·도강) 순환 당직과, 영기팀 평일 개별 당직을 함께 보여주는
정적 웹 캘린더입니다. 빌드 도구 없이 순수 HTML/CSS/JS로 되어 있어 GitHub Pages에
그대로 올려서 쓸 수 있습니다.

## 구성

```
index.html          캘린더 화면
style.css            스타일
app.js                화면 렌더링 + 수정 모드 + GitHub 저장 로직
data/schedule.json    당직 데이터 (이 파일을 GitHub에서 수정/커밋하면 사이트에 반영됨)
scripts/extract_xlsx.py   원본 엑셀 -> data/schedule.json 변환 스크립트
```

`data/schedule.json` 구조:

```json
{
  "meta": { "weekendRotation": ["영기", "소강", "도강"], "updatedAt": "..." },
  "weekendDuty": [
    { "date": "2026-09-05", "weekday": "토", "type": "휴일", "org": "영기", "person": "배병모", "note": "" }
  ],
  "weekdayDuty": [
    { "date": "2026-09-01", "weekday": "화", "type": "평일", "person": "임나현" }
  ]
}
```

## GitHub Pages로 배포하기

`main` 브랜치에 push될 때마다 `.github/workflows/deploy-pages.yml` 워크플로가
자동으로 GitHub Pages에 배포합니다 (별도 설정 없이 첫 실행 시 Pages가 자동으로
활성화됩니다). 배포가 끝나면 `https://<owner>.github.io/<repo>/` 로 접속하면
캘린더가 보입니다. 저장소 **Actions** 탭에서 배포 진행 상황을 확인할 수 있습니다.

## 보는 방법

- 상단 `‹ 이전달 / 다음달 ›` 로 월 이동, `이번달` 로 오늘로 복귀.
- 토요일/휴일: 담당 조직 배지(영기/소강/도강/전산휴무). 영기 조직인 날은
  담당자 이름까지 함께 표시되고, 비고가 있으면 아래에 작게 표시됩니다.
- 평일: 영기팀 개별 당직자 이름이 표시됩니다.
- 화면 하단 "다가오는 당직"에 앞으로 예정된 당직을 날짜순으로 모아 보여줍니다.

## 수정 모드 (일정 변경)

인원 변경, 순번 조정 등 수정 사항이 생기면 웹에서 바로 고쳐서 GitHub 저장소에
커밋할 수 있습니다.

1. 우측 상단 **⚙** 를 눌러 GitHub 연결 정보를 입력합니다.
   - 저장소 소유자(owner) / 저장소 이름(repo) / 브랜치(예: `main`)
   - Personal Access Token: GitHub → Settings → Developer settings →
     **Fine-grained personal access tokens** → 이 저장소만 선택 →
     Repository permissions에서 **Contents: Read and write** 권한을 준 토큰
   - 토큰은 이 브라우저의 localStorage에만 저장되며, GitHub API 호출 시에만
     사용됩니다. 공용 PC에서는 사용 후 브라우저 데이터를 지우는 것을 권장합니다.
2. 상단 **수정 모드** 버튼을 켭니다.
3. 캘린더에서 수정할 날짜를 클릭하면 편집 창이 뜹니다.
   - 구분(평일/휴일), 당직 조직, 영기 담당자, 비고, 평일 당직자를 고칠 수 있습니다.
   - **이 날짜 당직 비우기** 로 해당 날짜의 당직 정보를 삭제할 수 있습니다.
4. 수정을 마치면 상단 **GitHub에 저장** 버튼을 누릅니다. `data/schedule.json`
   파일이 커밋되고, GitHub Pages가 1분 내외로 재배포되면 모두에게 반영됩니다.

> 수정 모드는 브라우저에서 곧바로 저장소 파일을 커밋하는 방식이라 별도 서버가
> 필요 없습니다. 다만 토큰을 가진 사람만 저장할 수 있으므로, 팀 내에서 일정을
> 관리할 담당자만 토큰을 발급받아 사용하는 것을 권장합니다.

## 엑셀 파일이 새로 업데이트됐을 때

원본 엑셀(`26년 당직 일정_정리.xlsx`)의 "26년 당직 일정_정리" 시트 구조
(F~L열 주말/휴일 표, R~V열 평일 표)를 그대로 유지한 새 파일을 받았다면:

```bash
pip install openpyxl   # 최초 1회
python3 scripts/extract_xlsx.py /path/to/새엑셀.xlsx data/schedule.json
git add data/schedule.json
git commit -m "당직표 갱신"
git push
```

또는 위 수정 모드 UI로 하나씩 고쳐도 됩니다.
