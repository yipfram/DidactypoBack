# Imports standards Python
import logging
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Annotated, List, Optional
import asyncio
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Imports tiers
import jwt
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc
from fastapi import FastAPI, HTTPException, Depends, Query, Response, status, Request, Path as FastAPIPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from jwt.exceptions import InvalidTokenError
import time
from sqlalchemy.exc import SQLAlchemyError

# Imports internes
from database import SessionLocal, engine, execute_sql_file, is_initialized
from auth import Token, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM, pwd_context, oauth2_scheme, validate_password, is_common_password
import models
from pydantic_models import (
    IdClasses, UtilisateurBase,  UtilisateurModele,
    StatsUtilisateur, UtilisateurRenvoye,
    DefiBase, DefiModele,
    UtilisateurDefiBase, UtilisateurDefiModele,
    BadgeBase, BadgeModele,
    CoursBase, CoursModele,UtilisateurCoursBase,
    UtilisateurCoursModele,
    SousCoursBase, SousCoursModele,
    GroupeBase, GroupeModele,
    UtilisateurGroupeBase, UtilisateurGroupeModele,
    ExerciceBase, ExerciceModele,
    ExerciceUtilisateurBase, ExerciceUtilisateurModele,UpdateCptDefiRequest,
    PasswordChangeRequest, ProfilePicture, UpdatePdp,utilisateurPdp, UtilisateurCompte,
    ExerciceGroupeBase,ExerciceGroupeModel
)

app = FastAPI()
scheduler = BackgroundScheduler()



# Configuration CORS configuration to allow access from specific origins
origins = [
    'http://localhost:5173',
    'http://localhost:3000',
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration du logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dependency to get the DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables if they don't exist
models.Base.metadata.create_all(bind=engine)

@app.on_event("startup")
async def on_startup():
    sql_file_path = Path(__file__).parent / "cours.sql"
    exercices_sql_file_path = Path(__file__).parent / "exercices.sql"
    badges_sql_file_path = Path(__file__).parent / "badges.sql"
    defi_sql_file_path = Path(__file__).parent / "defi.sql"
    pp_sql_file_path = Path(__file__).parent / "photodeprofil.sql"
    scheduler.start()

    db = SessionLocal()

    print(is_initialized(db, models.ProfilePicture))  # Doit afficher False si pas encore initialisé
    try:
        if not is_initialized(db, models.Cours):
            # Run the SQL file only if the database is uninitialized
            execute_sql_file(sql_file_path)
            print("La base de donnée a été initialisée avec les données des cours.")
        else:
            print("Les données des cours sont déjà initialisées.")

        # Vérifie et initialise les données pour les exercices
        if not is_initialized(db, models.Exercice):
            execute_sql_file(exercices_sql_file_path)
            print("La base de données a été initialisée avec les données des exercices.")
        else:
            print("Les données des exercices sont déjà initialisées.")
        
        # Vérifie et initialise les données pour les badges
        if not is_initialized(db, models.Badge):
            execute_sql_file(badges_sql_file_path)
            print("La base de données a été initialisée avec les données des badges.")
        else:
            print("Les données des badges sont déjà initialisées.")

        # Vérifie et initialise les données pour les defis
        if not is_initialized(db, models.Defi):
            execute_sql_file(defi_sql_file_path)
            print("La base de données a été initialisée avec les données des defi.")
        else:
            print("Les données des badges sont déjà initialisées.")

        # Vérifie et initialise les données pour les photos de profil
        if not is_initialized(db, models.ProfilePicture): # Debug
            execute_sql_file(pp_sql_file_path)
            print("La base de données a été intialisée avec les données des photos de profil. ")
        else:
            print("Les données des photos de profil sont déjà initialisées.")
    finally:
        db.close()


def increment_weekly_challenge():
    try:
        db = SessionLocal()
        defi_semaine = db.query(models.DefiSemaine).first()
        
        if not defi_semaine:
            defi_semaine = models.DefiSemaine(numero_defi=1)
            db.add(defi_semaine)
            db.commit()
        else:
            attribuer_badges_classement(defi_semaine.numero_defi, db)
            defi_semaine.numero_defi += 1
            db.commit()
            print(f"✅ Nouveau numéro de défi : {defi_semaine.numero_defi}")
        
    except Exception as e:
        print(f"❌ Erreur lors de la mise à jour du défi : {str(e)}")
    finally:
        db.close()


# Modifier le scheduler pour utiliser directement la fonction synchrone
scheduler.add_job(
    increment_weekly_challenge,
    trigger=CronTrigger(day_of_week='mon', hour=4, minute=0),
    id='increment_weekly_challenge',
    replace_existing=True
)

# Fetch user logic
def get_utilisateur(db, pseudo: str):
    utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
    if utilisateur:
        return utilisateur
    return None

# Utilisateur Routes
@app.post('/utilisateurs/', response_model=UtilisateurModele)
async def creer_utilisateur(utilisateur: UtilisateurBase, db: Session = Depends(get_db)):
    try:
        # Validate the password security requirements
        is_valid, error_message = validate_password(utilisateur.mot_de_passe)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)
            
        # Check if password is in list of common/leaked passwords
        if is_common_password(utilisateur.mot_de_passe):
            raise HTTPException(
                status_code=400, 
                detail="Ce mot de passe est trop commun et facilement piratable. Veuillez choisir un mot de passe plus sécurisé."
            )
            
        # Hash the password before storing
        utilisateur.mot_de_passe = get_mdp_hashe(utilisateur.mot_de_passe)
        db_utilisateur = models.Utilisateur(
            pseudo=utilisateur.pseudo,
            mot_de_passe=utilisateur.mot_de_passe,
            nom=utilisateur.nom,
            prenom=utilisateur.prenom,
            courriel=utilisateur.courriel,
            est_admin=utilisateur.est_admin,
            numCours=utilisateur.numCours,
            tempsTotal=utilisateur.tempsTotal,
            cptDefi=0  # Valeur par défaut pour cptDefi
            )
        db.add(db_utilisateur)
        db.commit()
        db.refresh(db_utilisateur)
        return db_utilisateur
    except SQLAlchemyError as e:
        db.rollback()
        # Handle specific database errors
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except HTTPException as e:
        # Re-raise HTTP exceptions without modification
        raise e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur interne: {str(e)}")

@app.get('/utilisateurs/', response_model=List[UtilisateurRenvoye])
async def lire_utilisateurs(db: Session = Depends(get_db), skip: int = 0, limit: int = 100):
    try:
        utilisateurs = db.query(models.Utilisateur).offset(skip).limit(limit).all()
        if not utilisateurs:
            return Response(status_code=204)
        valUtilisateurs = [UtilisateurRenvoye(pseudo=user.pseudo, nom=user.nom, prenom=user.prenom, cptDefi=user.cptDefi) for user in utilisateurs]
        return valUtilisateurs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")
    
@app.get('/utilisateurCompte/{pseudo}', response_model=UtilisateurCompte)
async def lire_utilisateurCompte(pseudo: str ,db: Session = Depends(get_db), skip: int=0, limit: int = 100):
    try:         
        if pseudo:
            user = get_utilisateur(db, pseudo)
            if user:
                valUtilisateurCompte = UtilisateurCompte(
                    pseudo=user.pseudo,
                    nom=user.nom,  # Fixed typo: changed user.pseudo to user.nom
                    prenom=user.prenom,
                    courriel=user.courriel
                )
                return valUtilisateurCompte
            else:
                raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
        else:
            return Response(status_code=204)
        
    except HTTPException as e:
        raise e

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

@app.delete('/utilisateurs/{pseudo}', response_model=dict)
async def supprimer_utilisateur(pseudo: str, db: Session = Depends(get_db)):
    db_utilisateur = get_utilisateur(db, pseudo)
    if not db_utilisateur:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    
    db.delete(db_utilisateur)
    db.commit()
    return {"message": f"Utilisateur '{pseudo}' supprimé avec succès."}

@app.get('/utilisateurs/{pseudo}', response_model=UtilisateurModele)
async def lire_utilisateur(pseudo: str, db: Session = Depends(get_db)):
    try:
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            return Response(status_code=204)
        return utilisateur
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération de l'utilisateur : {str(e)}")

@app.get('/utilisateurPdp/{pseudo}', response_model=utilisateurPdp)
async def lire_pdp_utilisateur(pseudo: str, db: Session = Depends(get_db)):
    try:
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            return Response(status_code=204)
        return utilisateur
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération de l'utilisateur : {str(e)}")

@app.put('/utilisateurs/{pseudo}/cptDefi', response_model=utilisateurPdp)
async def mettre_a_jour_cpt_defi(
    pseudo: str,
    update_request: UpdateCptDefiRequest,  # Validation avec Pydantic
    db: Session = Depends(get_db)  # Injection de la session DB
):
    try:
        # Rechercher l'utilisateur dans la base de données
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        # Mettre à jour le champ `cptDefi`
        utilisateur.cptDefi = update_request.cptDefi
        db.commit()
        db.refresh(utilisateur)  # Recharger les données mises à jour depuis la DB
        return utilisateur
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour de cptDefi : {str(e)}")

@app.put('/utilisateurs/{pseudo}/pdp', response_model=utilisateurPdp)
async def mettre_a_jour_pdp(
    pseudo: str,
    update_request: UpdatePdp,  # Validation avec Pydantic
    db: Session = Depends(get_db)  # Injection de la session DB
):
    try:
        # Rechercher l'utilisateur dans la base de données
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        # Mettre à jour le champ `cptDefi`
        utilisateur.pdpActuelle = update_request.pdpActuelle
        db.commit()
        db.refresh(utilisateur)  # Recharger les données mises à jour depuis la DB
        return utilisateur
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour de la photo de profil : {str(e)}")

@app.patch("/modification_mdp")
async def modifier_mdp(request: PasswordChangeRequest, db: Session = Depends(get_db)):
    pseudo = request.pseudo
    ancien_mdp = request.ancien_mdp
    new_mdp = request.new_mdp
    # Vérification de la présence de l'utilisateur
    db_utilisateur = get_utilisateur(db, pseudo)
    if not db_utilisateur:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    
    # Vérification de l'ancien mot de passe
    if not (verifier_mdp(ancien_mdp,db_utilisateur.mot_de_passe)):
        raise HTTPException(status_code=401, detail="L'ancien mot de passe est incorrect")

    # Vérification du nouveau mot de passe
    if not new_mdp.strip():
        raise HTTPException(status_code=400, detail="Le nouveau mot de passe ne peut pas être vide")
    
    # Validate the new password security requirements
    is_valid, error_message = validate_password(new_mdp)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)
        
    # Check if password is in list of common/leaked passwords
    if is_common_password(new_mdp):
        raise HTTPException(
            status_code=400, 
            detail="Ce mot de passe est trop commun et facilement piratable. Veuillez choisir un mot de passe plus sécurisé."
        )
    
    # S'assurer que le nouveau mot de passe est différent de l'ancien
    if verifier_mdp(new_mdp, db_utilisateur.mot_de_passe):
        raise HTTPException(
            status_code=400, 
            detail="Le nouveau mot de passe doit être différent de l'ancien."
        )
    
    try:
        # Mise à jour du mot de passe
        db_utilisateur.mot_de_passe = get_mdp_hashe(new_mdp)
        db.commit()
        return {"message": f"Mot de passe de '{pseudo}' modifié avec succès."}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur interne, veuillez réessayer plus tard.")

# Admin logic
def is_admin(
        pseudo: str,
        db: Session
        ) -> bool:
    
    try:
        user = get_utilisateur(db, pseudo)
        
        if not user:
            return False
        
        if user.est_admin:
            return True
        else :
            raise HTTPException(status_code=403, detail="Accès restreint : vous n'êtes pas administrateur")
    
    except Exception as e:
        raise e
    

# Define password hash verification
def verifier_mdp(plain_password, mot_de_passe):
    return pwd_context.verify(plain_password, mot_de_passe)

def get_mdp_hashe(mot_de_passe):
    return pwd_context.hash(mot_de_passe)

def authenticate_user(db, pseudo: str, mot_de_passe: str):
    utilisateur = get_utilisateur(db, pseudo)
    if not utilisateur:
        return False
    if not verifier_mdp(mot_de_passe, utilisateur.mot_de_passe):
        return False
    return utilisateur

# Token creation logic
def creer_token_acces(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# JWT decode & user authentication
async def get_utilisateur_courant(token: Annotated[str, Depends(oauth2_scheme)], db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pseudo: str = payload.get("sub")
        if not pseudo:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials"
            )
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="L'utilisateur n'éxiste pas"
            )
        return utilisateur
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )

# Utilisateur Routes
@app.post('/utilisateurs/', response_model=UtilisateurModele)
async def creer_utilisateur(utilisateur: UtilisateurBase, db: Session = Depends(get_db)):
    try:
        # Validate the password security requirements
        is_valid, error_message = validate_password(utilisateur.mot_de_passe)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)
            
        # Check if password is in list of common/leaked passwords
        if is_common_password(utilisateur.mot_de_passe):
            raise HTTPException(
                status_code=400, 
                detail="Ce mot de passe est trop commun et facilement piratable. Veuillez choisir un mot de passe plus sécurisé."
            )
            
        # Hash the password before storing
        utilisateur.mot_de_passe = get_mdp_hashe(utilisateur.mot_de_passe)
        db_utilisateur = models.Utilisateur(
            pseudo=utilisateur.pseudo,
            mot_de_passe=utilisateur.mot_de_passe,
            nom=utilisateur.nom,
            prenom=utilisateur.prenom,
            courriel=utilisateur.courriel,
            est_admin=utilisateur.est_admin,
            numCours=utilisateur.numCours,
            tempsTotal=utilisateur.tempsTotal,
            cptDefi=0  # Valeur par défaut pour cptDefi
            )
        db.add(db_utilisateur)
        db.commit()
        db.refresh(db_utilisateur)
        return db_utilisateur
    except SQLAlchemyError as e:
        db.rollback()
        # Handle specific database errors
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except HTTPException as e:
        # Re-raise HTTP exceptions without modification
        raise e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur interne: {str(e)}")

@app.get('/utilisateurs/', response_model=List[UtilisateurRenvoye])
async def lire_utilisateurs(
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100):
    try:
        if is_admin(current_user.pseudo, db):
            utilisateurs = db.query(models.Utilisateur).offset(skip).limit(limit).all()
            if not utilisateurs:
                return Response(status_code=204)
            valUtilisateurs = [UtilisateurRenvoye(pseudo=user.pseudo, nom=user.nom, prenom=user.prenom, cptDefi=user.cptDefi) for user in utilisateurs]
            return valUtilisateurs
        
    except HTTPException as e:
        raise e
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

@app.delete('/utilisateurs/{pseudo}', response_model=dict)
async def supprimer_utilisateur(
    pseudo: str,
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db),
    ):
    if is_admin(current_user.pseudo, db):
        db_utilisateur = get_utilisateur(db, pseudo)
        if not db_utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
        
        db.delete(db_utilisateur)
        db.commit()
        return {"message": f"Utilisateur '{pseudo}' supprimé avec succès."}
        
@app.get('/utilisateur/{pseudo}', response_model=UtilisateurRenvoye)
async def  lire_utilisateur(
    pseudo: str,
    db: Session = Depends(get_db)
):
    try:
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            return Response(status_code=204)
        
        utilisateur_renvoye = UtilisateurRenvoye(pseudo=utilisateur.pseudo, nom=utilisateur.nom, prenom=utilisateur.prenom)

        return utilisateur_renvoye
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération de l'utilisateur : {str(e)}")

@app.get('/utilisateur_full/{pseudo}', response_model=UtilisateurModele)
async def lire_utilisateur_full(
    pseudo: str,
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db),
    ):
    try :
        if is_admin(current_user.pseudo, db):
            try:
                utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
                if not utilisateur:
                    return Response(status_code=204)
                return utilisateur
            
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération de l'utilisateur : {str(e)}")
    except Exception as e:
        raise e


@app.put('/utilisateurs/{pseudo}/cptDefi', response_model=UtilisateurModele)
async def mettre_a_jour_cpt_defi(
    pseudo: str,
    update_request: UpdateCptDefiRequest,  # Validation avec Pydantic
    db: Session = Depends(get_db)  # Injection de la session DB
):
    try:
        # Rechercher l'utilisateur dans la base de données
        utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        # Mettre à jour le champ `cptDefi`
        utilisateur.cptDefi = update_request.cptDefi
        db.commit()
        db.refresh(utilisateur)  # Recharger les données mises à jour depuis la DB
        return utilisateur
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour de cptDefi : {str(e)}")


@app.patch("/modification_mdp")
async def modifier_mdp(request: PasswordChangeRequest, db: Session = Depends(get_db)):
    pseudo = request.pseudo
    ancien_mdp = request.ancien_mdp
    new_mdp = request.new_mdp
    # Vérification de la présence de l'utilisateur
    db_utilisateur = get_utilisateur(db, pseudo)
    if not db_utilisateur:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    
    # Vérification de l'ancien mot de passe
    if not (verifier_mdp(ancien_mdp,db_utilisateur.mot_de_passe)):
        raise HTTPException(status_code=401, detail="L'ancien mot de passe est incorrect")

    # Vérification du nouveau mot de passe
    if not new_mdp.strip():
        raise HTTPException(status_code=400, detail="Le nouveau mot de passe ne peut pas être vide")
    
    # Validate the new password security requirements
    is_valid, error_message = validate_password(new_mdp)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)
        
    # Check if password is in list of common/leaked passwords
    if is_common_password(new_mdp):
        raise HTTPException(
            status_code=400, 
            detail="Ce mot de passe est trop commun et facilement piratable. Veuillez choisir un mot de passe plus sécurisé."
        )
    
    # S'assurer que le nouveau mot de passe est différent de l'ancien
    if verifier_mdp(new_mdp, db_utilisateur.mot_de_passe):
        raise HTTPException(
            status_code=400, 
            detail="Le nouveau mot de passe doit être différent de l'ancien."
        )
    
    try:
        # Mise à jour du mot de passe
        db_utilisateur.mot_de_passe = get_mdp_hashe(new_mdp)
        db.commit()
        return {"message": f"Mot de passe de '{pseudo}' modifié avec succès."}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur interne, veuillez réessayer plus tard.")

#/default/lire_utilisateurs_utilisateurs__get
# Token endpoint
@app.post("/token")
async def login_pour_token_acces(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db=Depends(get_db)
) -> Token:
    utilisateur = authenticate_user(db, form_data.username, form_data.password)
    if not utilisateur:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mot de passe ou pseudo incorrecte",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = creer_token_acces(
        data={"sub": utilisateur.pseudo}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")

@app.get('/utilisateur/moi')
async def lire_utilisateur_courant(
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)]
):
    nom = current_user.nom
    prenom = current_user.prenom
    pseudo = current_user.pseudo
    return UtilisateurRenvoye(pseudo=pseudo, nom=nom, prenom=prenom)

# Defi Routes
@app.post('/defis/', response_model=DefiModele)
async def ajouter_defi(defi: DefiBase, db: Session = Depends(get_db)):
    try:
        db_defi = models.Defi(**defi.dict())
        db.add(db_defi)
        db.commit()
        db.refresh(db_defi)
        return db_defi
    except Exception as e:
        db.rollback()  # Rollback the transaction if an error occurs
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'ajout du défi : {str(e)}")

@app.get('/defis/', response_model=List[DefiModele])
async def lire_defis(db: Session = Depends(get_db), skip: int = 0, limit: int = 1000):
    defis = db.query(models.Defi).offset(skip).limit(limit).all()
    return defis

@app.get('/defis/{id_defi}', response_model=DefiModele)
async def lire_infos_defi(id_defi: int, db: Session = Depends(get_db)):
    defis = db.query(models.Defi).filter(models.Defi.id_defi == id_defi).first()
    return defis

@app.delete('/defis/{id_defi}', response_model=dict)
async def supprimer_defi(id_defi: int, db: Session = Depends(get_db)):
    # Récupérer le défi en fonction de son id
    db_defi = db.query(models.Defi).filter(models.Defi.id_defi == id_defi).first()
    
    # Si le défi n'est pass trouvé, erreur 404
    if not db_defi:
        raise HTTPException(status_code=404, detail="Défi non trouvé")
    
    # Récupérer titre du défi pour le message de succès
    titre_defi = db_defi.titre_defi
    
    # Supprimer le défi
    db.delete(db_defi)
    db.commit()
    
    # Message de réussiyte
    return {"message": f"Défi '{titre_defi}' supprimé avec succès."}

#Réussite défi

@app.post('/reussites_defi/', response_model=UtilisateurDefiModele)
async def ajout_reussite_defi(
    id_defi: int,  # ID du défi (passé en paramètre de la requête)
    temps_reussite: float,  # Temps de réussite du défi
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db)  # Dépendance pour obtenir la session de base de données
):
    try:
        # Vérifier si l'utilisateur existe dans la base de données
        db_utilisateur = get_utilisateur(db, current_user.pseudo)

        # Vérifier si le défi existe dans la base de données
        db_defi = db.query(models.Defi).filter(models.Defi.id_defi == id_defi).first()
        if not db_defi:
            raise HTTPException(status_code=404, detail="Défi non trouvé")
    
        db_utilisateur_defi = models.UtilisateurDefi(
            pseudo_utilisateur=current_user.pseudo,
            id_defi=id_defi,
            temps_reussite=temps_reussite,
            date_reussite=datetime.now()  # Définir la date de réussite à l'heure actuelle
        )
        db.add(db_utilisateur_defi)  # Ajouter la nouvelle réussite dans la base de données
        db.commit()  # Commit les changements
        db.refresh(db_utilisateur_defi)  # Rafraîchir l'instance pour obtenir les données mises à jour
        return db_utilisateur_defi  # Retourner la nouvelle réussite ajoutée
    
    except Exception as e:
        # Si une erreur se produit, annuler la transaction et retourner un message d'erreur
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'ajout de la réussite du défi : {str(e)}")

#Récupérer toutes les réussites de défi
@app.get('/reussites_defi', response_model=List[UtilisateurDefiModele])
async def lire_reussite_defi(
    db: Session = Depends(get_db),  # Dépendance pour obtenir la session de base de données
    skip: int = 0,  # Paramètre optionnel pour le décalage (pagination)
    limit: int = 100  # Paramètre optionnel pour la limite du nombre de résultats
):
    try:
        # Récupérer toutes les réussites de défi pour tous les utilisateurs
        reussites_defi = db.query(models.UtilisateurDefi).offset(skip).limit(limit).all()
        # Subquery to get the minimum time for each user and challenge
        subquery = db.query(
            models.UtilisateurDefi.pseudo_utilisateur,
            models.UtilisateurDefi.id_defi,
            func.min(models.UtilisateurDefi.temps_reussite).label('min_temps_reussite')
        ).group_by(
            models.UtilisateurDefi.pseudo_utilisateur,
            models.UtilisateurDefi.id_defi
        ).subquery()

        # Join the subquery with the main table to get the full records
        reussites_defi = db.query(models.UtilisateurDefi).join(
            subquery,
            (models.UtilisateurDefi.pseudo_utilisateur == subquery.c.pseudo_utilisateur) &
            (models.UtilisateurDefi.id_defi == subquery.c.id_defi) &
            (models.UtilisateurDefi.temps_reussite == subquery.c.min_temps_reussite)
        ).offset(skip).limit(limit).all()

        # Si aucune réussite de défi n'est trouvée
        if not reussites_defi:
            return Response(status_code=204) 
        
        return reussites_defi  # Retourner la liste complète des réussites de défi
    
    except Exception as e:
        # Gestion des erreurs (rollback en cas d'exception)
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des réussites de défi : {str(e)}")

#Récuperer les réussites de défi d'un utilisateur
@app.get('/reussites_defi/utilisateurs/{pseudo_utilisateur}', response_model=List[UtilisateurDefiModele])
async def lire_reussite_defi_utilisateur(
    pseudo_utilisateur: str,  # Pseudo de l'utilisateur passé en paramètre de l'URL


    id_defi: int = None,  # Paramètre optionnel pour filtrer par défi spécifique

    # mettre au dessus la valeur de l'id actuel

    db: Session = Depends(get_db),  # Dépendance pour obtenir la session de base de données
    skip: int = 0,  # Paramètre optionnel pour le décalage (pagination)
    limit: int = 100  # Paramètre optionnel pour la limite du nombre de résultats
):
    try:
        # Commencer la requête de base
        query = db.query(models.UtilisateurDefi).filter(
            models.UtilisateurDefi.pseudo_utilisateur == pseudo_utilisateur
        )

        # Si un id_defi est fourni, ajouter le filtre correspondant
        if id_defi is not None:
            query = query.filter(models.UtilisateurDefi.id_defi == id_defi)

        # Appliquer la pagination et récupérer les résultats
        reussites_defi = query.offset(skip).limit(limit).all()

        # Si aucune réussite n'est trouvée
        if not reussites_defi:
            raise HTTPException(
                status_code=404, 
                detail="Aucune réussite de défi trouvée pour cet utilisateur" + 
                       (f" et ce défi" if id_defi else ".")
            )
        
        return reussites_defi

    except Exception as e:
        # Gestion des erreurs (rollback en cas d'exception)
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des réussites de défi : {str(e)}")
    
# Récupérer les résuiste défi par défi
@app.get('/reussites_defi/defi/{id_defi}', response_model=List[UtilisateurDefiModele])
async def lire_reussite_defi_utilisateur_id_defi(
    id_defi: int,  # id du défi passé en paramètre de l'URL
    db: Session = Depends(get_db),  # Dépendance pour obtenir la session de base de données
    skip: int = 0,  # Paramètre optionnel pour le décalage (pagination)
    limit: int = 100  # Paramètre optionnel pour la limite du nombre de résultats
):
    try:
        # Sous-requête pour obtenir le meilleur temps de chaque utilisateur pour ce défi
        subquery = db.query(
            models.UtilisateurDefi.pseudo_utilisateur,
            func.min(models.UtilisateurDefi.temps_reussite).label('min_temps_reussite')
        ).filter(
            models.UtilisateurDefi.id_defi == id_defi
        ).group_by(
            models.UtilisateurDefi.pseudo_utilisateur
        ).subquery()

        # Requête principale qui joint avec la sous-requête pour obtenir les enregistrements complets
        reussites_defi = db.query(models.UtilisateurDefi).join(
            subquery,
            (models.UtilisateurDefi.pseudo_utilisateur == subquery.c.pseudo_utilisateur) &
            (models.UtilisateurDefi.temps_reussite == subquery.c.min_temps_reussite)
        ).filter(
            models.UtilisateurDefi.id_defi == id_defi
        ).order_by(
            models.UtilisateurDefi.temps_reussite  # Tri par temps croissant
        ).offset(skip).limit(limit).all()

        # Si aucune réussite n'est trouvée pour cet utilisateur
        if not reussites_defi:
            raise HTTPException(status_code=404, detail="Aucune réussite de défi trouvée pour cet utilisateur.")
        
        return reussites_defi  # Retourner la liste des réussites de défi
    
    except Exception as e:
        # Gestion des erreurs (rollback en cas d'exception)
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des réussites de défi : {str(e)}")

#Supprimer une réussite de défi
@app.delete('/reussites_defi/', response_model=dict)
async def supprimer_reussite_defi(
    pseudo_utilisateur: str,  # Pseudo de l'utilisateur dont la réussite sera supprimée
    id_defi: int,  # ID du défi de la réussite à supprimer
    db: Session = Depends(get_db)  # Dépendance pour obtenir la session de base de données
):
    try:
        # Récupérer la réussite de défi spécifique en fonction du pseudo de l'utilisateur et de l'ID du défi
        reussite_defi = db.query(models.UtilisateurDefi).filter(
            models.UtilisateurDefi.pseudo_utilisateur == pseudo_utilisateur,
            models.UtilisateurDefi.id_defi == id_defi
        ).first()

        # Si la réussite n'est pas trouvée, renvoyer une erreur 404
        if not reussite_defi:
            raise HTTPException(status_code=404, detail="Réussite de défi non trouvée.")
        
        # Supprimer l'élément trouvé
        db.delete(reussite_defi)
        db.commit()
        
        # Retourner un message de succès
        return {"message": f"La réussite du défi avec l'ID {id_defi} pour l'utilisateur '{pseudo_utilisateur}' a été supprimée avec succès."}
    
    except Exception as e:
        # Gestion des erreurs (rollback en cas d'exception)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la suppression de la réussite du défi : {str(e)}")

#Cours
@app.post('/cours/', response_model=CoursModele)
async def ajouter_cour(cour: CoursBase, db: Session = Depends(get_db)):
    try:
        db_cour = models.Cours(**cour.dict())
        db.add(db_cour)
        db.commit()
        db.refresh(db_cour)
        return db_cour
    except Exception as e:
        db.rollback()  # Rollback the transaction if an error occurs
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'ajout du cours : {str(e)}")

@app.get('/cours/', response_model=List[CoursModele])
async def lire_cours(db: Session = Depends(get_db), skip: int = 0, limit: int = 100):
    cours = db.query(models.Cours).offset(skip).limit(limit).all()
    return cours

@app.get('/cours/{id_cour}', response_model=CoursModele)
async def lire_infos_cour(id_cour: int, db: Session = Depends(get_db)):
    cours = db.query(models.Cours).filter(models.Cours.id_cours == id_cour).first()
    return cours

@app.delete('/cours/{id_cour}', response_model=dict)
async def supprimer_cour(id_cour: int, db: Session = Depends(get_db)):
    # Récupérer le cours en fonction de son id
    db_cour = db.query(models.Cours).filter(models.Cours.id_cours == id_cour).first()
    
    # Si le cours n'est pas trouvé, erreur 404
    if not db_cour:
        raise HTTPException(status_code=404, detail="Défi non trouvé")
    
    # Récupérer titre du cours pour le message de succès
    titre_cour = db_cour.titre_cours
    
    # Supprimer le cours
    db.delete(db_cour)
    db.commit()
    
    # Message de réussiyte
    return {"message": f"Défi '{titre_cour}' supprimé avec succès."}

#Sous cours
def get_next_sous_cours_id(db: Session, id_cours_parent: int) -> int:
    # Find the maximum existing `id_sous_cours` for this `id_cours_parent`
    max_id_query = db.query(func.max(models.SousCours.id_sous_cours)).filter(models.SousCours.id_cours_parent == id_cours_parent)
    max_id = max_id_query.scalar()

    # If there are no existing records for this parent, start at 1
    return max_id + 1 if max_id else 1

@app.post("/sous_cours/", response_model=SousCoursModele)
async def add_sous_cours(sous_cours_data: SousCoursBase, db: Session = Depends(get_db)):
    # Check if the parent course exists
    parent_cours = db.query(models.Cours).filter(models.Cours.id_cours == sous_cours_data.id_cours_parent).first()
    if not parent_cours:
        raise HTTPException(status_code=404, detail="Parent course not found")
    
    # Generate the next SousCours ID specifically for this course
    next_id = get_next_sous_cours_id(db, sous_cours_data.id_cours_parent)

    # Create the new sous_cours
    new_sous_cours = models.SousCours(
        id_cours_parent=sous_cours_data.id_cours_parent,
        id_sous_cours=next_id,
        titre_sous_cours=sous_cours_data.titre_sous_cours,
        contenu_cours=sous_cours_data.contenu_cours,
        chemin_img_sous_cours=sous_cours_data.chemin_img_sous_cours
    )

    try:
        db.add(new_sous_cours)
        db.commit()
        db.refresh(new_sous_cours)
    except Exception as e:
        db.rollback()  # Rollback en cas d'erreur
        raise HTTPException(status_code=500, detail=f"Error while adding sub-course: {str(e)}")

    return new_sous_cours

@app.get("/sous_cours/{id_cours_parent}", response_model=List[SousCoursModele])
async def get_sous_cours_by_parent(id_cours_parent: int, db: Session = Depends(get_db)):
    sous_cours_list = db.query(models.SousCours).filter(models.SousCours.id_cours_parent == id_cours_parent).all()
    
    if not sous_cours_list:
        raise HTTPException(status_code=404, detail="Aucun sous-cours trouvé pour ce parent")
    
    return sous_cours_list

@app.get("/sous_cours", response_model=SousCoursModele)
def get_sous_cours(id_sous_cours: int, id_cours_parent: int, db: Session = Depends(get_db)) -> models.SousCours:
    sous_cours = db.query(models.SousCours).filter(
        models.SousCours.id_sous_cours == id_sous_cours,
        models.SousCours.id_cours_parent == id_cours_parent
    ).first()

    if not sous_cours:
        raise HTTPException(status_code=404, detail="Sous-cours non trouvé")
    
    return sous_cours

@app.delete("/sous_cours/{id_sous_cours}", response_model=dict)
def delete_sous_cours(id_sous_cours: int, id_cours_parent: int, db: Session = Depends(get_db)):
    sous_cours = db.query(models.SousCours).filter(
        models.SousCours.id_sous_cours == id_sous_cours,
        models.SousCours.id_cours_parent == id_cours_parent
    ).first()

    if not sous_cours:
        raise HTTPException(status_code=404, detail="Sous-cours non trouvé")
    
    # Delete the sous_cours if found
    db.delete(sous_cours)
    db.commit()

    return {"message": f"Sous-cours avec ID {id_sous_cours} et ID parent {id_cours_parent} supprimé."}

#complétion cours
@app.post('/completion_cours', response_model=UtilisateurCoursModele)
async def ajouter_completion_cours(
    completion: UtilisateurCoursBase,
    db: Session = Depends(get_db)
):
    # Vérifier si une progression existe déjà pour ce cours et cet utilisateur
    existing_completion = db.query(models.UtilisateurCours).filter(
        models.UtilisateurCours.pseudo_utilisateur == completion.pseudo_utilisateur,
        models.UtilisateurCours.id_cours == completion.id_cours
    ).first()
    
    if existing_completion:
        # Si une progression existe déjà, on retourne simplement celle-ci
        return existing_completion
        
    # Si pas de progression existante, on continue avec la création
    new_completion = models.UtilisateurCours(
        id_cours=completion.id_cours,
        pseudo_utilisateur=completion.pseudo_utilisateur,
        progression=completion.progression
    )

    try:
        db.add(new_completion)
        db.commit()
        db.refresh(new_completion)
        
        # Vérifier si tous les cours sont terminés
        total_cours = db.query(models.Cours).count()
        cours_completes = db.query(models.UtilisateurCours).filter_by(
            pseudo_utilisateur=completion.pseudo_utilisateur,
            progression=100
        ).count()

        if cours_completes == total_cours:
            await ajout_gain_badge(
                pseudo_utilisateur=completion.pseudo_utilisateur,
                id_badge=8,
                db=db
            )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur pendant l'ajout de la completion: {str(e)}")

    return new_completion

# Groupes
@app.post('/groupe/', response_model=GroupeBase)
async def ajouter_groupe(
    groupe: GroupeBase,
    pseudo_admin: str,
    db: Session = Depends(get_db)):
    try:
        # Case 1: Ensure the group data is valid
        if not groupe:
            raise HTTPException(status_code=400, detail="Données de groupe invalides")
    

        # Create new group
        db_groupe = models.Groupe(**groupe.dict())
        db.add(db_groupe)
        db.commit()
        db.refresh(db_groupe)
        
        # Case 3: Check if the group ID was correctly generated
        if not db_groupe.id_groupe:
            raise HTTPException(status_code=500, detail="Erreur de création du groupe: ID non généré")
        
        # Case 4: Check if the admin user exists in the database
        existing_user = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo_admin).first()
        if not existing_user:
            raise HTTPException(status_code=404, detail=f"Utilisateur avec le pseudo '{pseudo_admin}' non trouvé")

        # Add the user to the group
        db_utilisateur_groupe = models.UtilisateurGroupe(
            pseudo_utilisateur=pseudo_admin,
            id_groupe=db_groupe.id_groupe,
            est_admin=True,
        )
        
        db.add(db_utilisateur_groupe)
        db.commit()
        db.refresh(db_utilisateur_groupe)

        return db_groupe

    except Exception as e:
        db.rollback()  # Rollback in case of any error
        # Case 8: General error handling
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'ajout du groupe : {str(e)}")



@app.get('/groupe/', response_model=List[GroupeModele])
async def lire_groupe(db: Session = Depends(get_db), skip: int = 0, limit: int = 100):
    groupe = db.query(models.Groupe).offset(skip).limit(limit).all()
    return groupe

@app.get('/groupe/{id_groupe}', response_model=GroupeModele)
async def lire_infos_groupe(id_groupe: int, db: Session = Depends(get_db)):
    groupe = db.query(models.Groupe).filter(models.Groupe.id_groupe == id_groupe).first()
    if groupe:
        return groupe
    else :
        return Response(status_code=204)

@app.delete('/groupe/{id_groupe}', response_model=dict)
async def supprimer_groupe(id_groupe: int, db: Session = Depends(get_db)):
    # Récupérer le groupe en fonction de son id
    db_groupe = db.query(models.Groupe).filter(models.Groupe.id_groupe == id_groupe).first()
    
    # Si le groupe n'est pas trouvé, erreur 404
    if not db_groupe:
        raise HTTPException(status_code=404, detail="groupe non trouvé")
    
    # Récupérer nom du groupe pour le message de succès
    nom_groupe = db_groupe.nom_groupe
    
    # Supprimer le groupe
    db.delete(db_groupe)
    db.commit()
    
    # Message de réussite
    return {"message": f"groupe '{nom_groupe}' supprimé avec succès."}



#Groupe d'utilisateurs

@app.post('/membre_classe/', response_model=UtilisateurGroupeModele)
async def ajout_membre_classe(
    id_groupe : int,  # ID du groupe (passé en paramètre de la requête)
    pseudo_utilisateur: str,  # Pseudo de l'utilisateur (passé en paramètre de la requête)
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    est_admin : bool,  # booleen pour definir l'admin du groupe
    db: Session = Depends(get_db)  # Dépendance pour obtenir la session de base de données
):
    try:
        # Vérifier si l'utilisateur est admin de la classe ou éssaie de s'ajouter eux-même
        admin_of_class = db.query(models.UtilisateurGroupe).filter(
            (models.UtilisateurGroupe.est_admin == True) & 
            (models.UtilisateurGroupe.pseudo_utilisateur == current_user.pseudo) & 
            (models.UtilisateurGroupe.id_groupe == id_groupe)
        ).first()

        if (admin_of_class or current_user.pseudo == pseudo_utilisateur):

            # Vérifier si l'utilisateur existe dans la base de données
            db_utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo_utilisateur).first()
            if not db_utilisateur:
                raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
            
            # Vérifier si le défi existe dans la base de données
            db_groupe = db.query(models.Groupe).filter(models.Groupe.id_groupe == id_groupe).first()
            if not db_groupe:
                raise HTTPException(status_code=404, detail="Groupe non trouvé")
            
            # Vérifier si l'utilisateur appartient deja à ce groupe
            existing_reussite = db.query(models.UtilisateurGroupe).filter(
                models.UtilisateurGroupe.pseudo_utilisateur == pseudo_utilisateur,
                models.UtilisateurGroupe.id_groupe == id_groupe
            ).first()

            if existing_reussite:
                # Si l'utilisateur appartient à ce groupe
                return existing_reussite  # Voir quelle action effectuer ici
            else:
                # Si l'utilisateur n'appartient pas a ce groupe, l'ajouter au groupe
                db_utilisateur_groupe = models.UtilisateurGroupe(
                    pseudo_utilisateur=pseudo_utilisateur,
                    id_groupe=id_groupe,
                    est_admin=est_admin,
                )
                db.add(db_utilisateur_groupe)  # Ajouter la nouvelle réussite dans la base de données
                db.commit()  # Commit les changements
                db.refresh(db_utilisateur_groupe)  # Rafraîchir l'instance pour obtenir les données mises à jour
                return db_utilisateur_groupe  # Retourner la nouvelle réussite ajoutée
        else:
            raise HTTPException(status_code=403, detail="Vous n'avez pas la permission d'ajouter cet utilisateur à cette classe")
    
    except HTTPException as e:
        raise e
    
    except Exception as e:
        # Si une erreur se produit, annuler la transaction et retourner un message d'erreur
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'ajout de la réussite du défi : {str(e)}")


@app.get('/admins_par_groupe/{id_groupe}', response_model=List[UtilisateurRenvoye])
async def lire_admin_groupe(
    id_groupe: int,
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db),  # Dépendance pour obtenir la session de base de données
    skip: int = 0,  # Paramètre optionnel pour le décalage (pagination)
    limit: int = 100  # Paramètre optionnel pour la limite du nombre de résultats
):
    try:
        # Ne retourner les infos uniquement si l'utilisateur fait lui même parti de cette classe
        membre_groupe = db.query(models.UtilisateurGroupe).filter(
            (models.UtilisateurGroupe.pseudo_utilisateur == current_user.pseudo) &
            (models.UtilisateurGroupe.id_groupe == id_groupe)
            ).first()
        
        if not membre_groupe:
            raise HTTPException(status_code=403, detail="Accès restreint : vous ne faites pas parti de cette classe")
        
        else :
            # Récupérer les utilisateurs du groupe qui sont des administrateurs
            pseudo_admins = db.query(models.UtilisateurGroupe).filter(
                (models.UtilisateurGroupe.id_groupe == id_groupe) & 
                (models.UtilisateurGroupe.est_admin == True)
            ).offset(skip).limit(limit).all()
            
            if not pseudo_admins:
                raise HTTPException(status_code=404, detail=f"Aucun administrateur dans la classe : {id_groupe}")
            
            # Récupérer les informations des administrateurs
            db_admins = db.query(models.Utilisateur).join(
                models.UtilisateurGroupe, 
                models.Utilisateur.pseudo == models.UtilisateurGroupe.pseudo_utilisateur
            ).filter(
                models.UtilisateurGroupe.id_groupe == id_groupe, 
                models.UtilisateurGroupe.est_admin == True
            ).offset(skip).limit(limit).all()
            
            if not db_admins:
                raise HTTPException(status_code=404, detail="Aucune information trouvée pour les administrateurs")
            
            # Retourner les informations des administrateurs sous forme de liste d'objets UtilisateurRenvoye
            infos_admins = [UtilisateurRenvoye(pseudo=admin.pseudo, nom=admin.nom, prenom=admin.prenom) for admin in db_admins]
            
            return infos_admins
    
    except HTTPException as e:
        raise e
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des relations groupe-utilisateur : {str(e)}")

@app.get('/membres_classe_par_groupe/{id_groupe}', response_model=List[UtilisateurRenvoye])
async def lire_membres_classe_groupe(
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    id_groupe: int,
    db: Session = Depends(get_db),  # Dépendance pour obtenir la session de base de données
    skip: int = 0,  # Paramètre optionnel pour le décalage (pagination)
    limit: int = 100,  # Paramètre optionnel pour la limite du nombre de résultats
):
    try:
        # Ne retourner ces infos que si l'utilisateur fait partie de cette classe
        membre_groupe = db.query(models.UtilisateurGroupe).filter(
            (models.UtilisateurGroupe.pseudo_utilisateur == current_user.pseudo) &
            (models.UtilisateurGroupe.id_groupe == id_groupe)
            ).first()
        
        if not membre_groupe:
            raise HTTPException(status_code=403, detail="Accès restreint : vous ne faites pas partie de cette classe")
        # Récupérer les utilisateurs du groupe qui ne sont pas des administrateurs
        pseudo_membres = db.query(models.UtilisateurGroupe).filter(
            (models.UtilisateurGroupe.id_groupe == id_groupe) & 
            (models.UtilisateurGroupe.est_admin == False)
        ).offset(skip).limit(limit).all()
        
        if not pseudo_membres:
            raise HTTPException(status_code=404, detail=f"Aucun membre dans la classe : {id_groupe}")
        
        # Renvoyer les ifnos des membres
        db_membres = db.query(models.Utilisateur).join(
            models.UtilisateurGroupe,
            models.Utilisateur.pseudo == models.UtilisateurGroupe.pseudo_utilisateur
        ).filter(
            models.UtilisateurGroupe.id_groupe == id_groupe,
            models.UtilisateurGroupe.est_admin == False
        ).offset(skip).limit(limit).all()

        if not db_membres:
            return Response(status_code=204)

        infos_membres = [UtilisateurRenvoye(pseudo=membre.pseudo, nom=membre.nom, prenom=membre.prenom) for membre in db_membres]

        return infos_membres
      
    except HTTPException as e:
        raise e
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des relations groupe-utilisateur : {str(e)}")


def get_admin_count(
    id_groupe: int,
    db
):
    # Comptage des administrateurs dans le groupe sans pagination
    total_admins = db.query(models.UtilisateurGroupe).filter(
        (models.UtilisateurGroupe.id_groupe == id_groupe) & 
        (models.UtilisateurGroupe.est_admin == True)
    ).count()

    # Retourner le nombre total d'administrateurs dans le groupe
    return total_admins
    
@app.get('/membre_est_admin/{id_groupe}', response_model=bool)
async def verifier_admin_classe(
    id_groupe: int,  # ID du groupe à vérifier
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db)
):
    try:
        # Vérifier si l'utilisateur courant est admin de la classe
        lien_utilisateur_courant_classe = db.query(models.UtilisateurGroupe).filter(
            models.UtilisateurGroupe.id_groupe == id_groupe,
            models.UtilisateurGroupe.pseudo_utilisateur == current_user.pseudo,
            models.UtilisateurGroupe.est_admin == True
        ).first()
        if lien_utilisateur_courant_classe:
            return True
        else:
            return False

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la vérification du statut administrateur : {str(e)}")
    
@app.patch('/admin_classe/', response_model=dict)
async def changer_admin_classe(
    id_groupe: int,  # ID du groupe à donner
    pseudo_utilisateur: str,  # Pseudo de l'utilisateur à promouvoir/démouvoir
    est_admin: bool,  # Booléen pour promouvoir/démouvoir l'utilisateur
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db)
):
    try:
        #Vérifier si l'utilisateur change son propre statut
        if pseudo_utilisateur == current_user.pseudo:
            raise HTTPException(status_code=403, detail="Vous ne pouvez pas changer votre propre statut administrateur")
        
        # Vérifier si l'utilisateur courant est admin de la classe
        lien_utilisateur_courant_classe = db.query(models.UtilisateurGroupe).filter(
            models.UtilisateurGroupe.id_groupe == id_groupe,
            models.UtilisateurGroupe.pseudo_utilisateur == current_user.pseudo,
            models.UtilisateurGroupe.est_admin == True
        ).first()
        if not lien_utilisateur_courant_classe:
            raise HTTPException(status_code=403, detail="Accès refusé : Vous devez être administrateur de cette classe")

        # Vérifier si l'utilisateur à promouvoir/démouvoir existe et est dans la classe
        utilisateur = get_utilisateur(db, pseudo_utilisateur)
        if not utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
        
        lien_utilisateur_classe = db.query(models.UtilisateurGroupe).filter(
            models.UtilisateurGroupe.id_groupe == id_groupe,
            models.UtilisateurGroupe.pseudo_utilisateur == pseudo_utilisateur
        ).first()
        if not lien_utilisateur_classe:
            raise HTTPException(status_code=404, detail="L'utilisateur n'est pas membre de cette classe")

        if est_admin:
            # Cas 1 : Promouvoir l'utilisateur en tant qu'administrateur
            # Vérifier si l'utilisateur est déjà administrateur
            if lien_utilisateur_classe.est_admin:
                return {"message": f"L'utilisateur '{pseudo_utilisateur}' est déjà administrateur de la classe"}
            
            # Promouvoir l'utilisateur en tant qu'administrateur
            lien_utilisateur_classe.est_admin = est_admin
            db.commit()

            return {"message": f"Utilisateur '{pseudo_utilisateur}' promu administrateur de la classe"}
        
        else:
            # Cas 2 : Démouvoir l'administrateur
            # Vérifier si l'utilisateur est déjà un membre normal
            if not lien_utilisateur_classe.est_admin:
                return {"message": f"L'utilisateur '{pseudo_utilisateur}' est déjà un membre normal de la classe"}
            
            # Démouvoir l'administrateur
            lien_utilisateur_classe.est_admin = est_admin
            db.commit()

            return {"message": f"Statut administrateur mis à jour pour l'utilisateur '{pseudo_utilisateur}'"}

    except HTTPException as e:
        raise e

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour du statut administrateur : {str(e)}")

@app.get('/membre_classe/{pseudo_utilisateur}', response_model=UtilisateurGroupeModele)
async def lire_groupes_d_utilisateur(
    pseudo_utilisateur: str,  # Pseudo de l'utilisateur dont on veut les groupes
    db: Session = Depends(get_db),  # Dépendance pour obtenir la session de base de données
):
    """
    Retourne les informations de l'utilisateur dans le groupe
    """
    try:
        # Récupérer le groupe auxquel appartient l'utilisateur
        groupe_utilisateur = db.query(models.UtilisateurGroupe).filter(
            models.UtilisateurGroupe.pseudo_utilisateur == pseudo_utilisateur
        ).first()

        # Si aucun groupe n'est trouvé pour cet utilisateur
        if not groupe_utilisateur:
            return Response(status_code=204)
        
        return groupe_utilisateur  # Retourner la liste des groupes de l'utilisateur
    
    except Exception as e:
        # Gestion des erreurs
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des groupes de l'utilisateur : {str(e)}")
    
@app.get('/membre_classes/{pseudo_utilisateur}', response_model=List[GroupeModele])
async def lire_classes_utilisateur(pseudo_utilisateur: str, db: Session = Depends(get_db)):
    """
    Récupère la liste des classes auxquelles appartient un utilisateur
    """
    try:
        groupes_utilisateur = db.query(models.UtilisateurGroupe).options(
            joinedload(models.UtilisateurGroupe.groupe)
        ).filter(
            models.UtilisateurGroupe.pseudo_utilisateur == pseudo_utilisateur
        ).all()

        if not groupes_utilisateur:
            return []

        return [groupe.groupe for groupe in groupes_utilisateur]

    except Exception as e:
        db.rollback()  # Annulation de la transaction en cas d'erreur
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des classes : {str(e)}")


@app.delete('/membres_classe', response_model=dict)
async def supprimer_relation_utilisateur_groupe(
    id_groupe: int,  # ID du groupe à supprimer
    pseudo_utilisateur: str,  # Pseudo de l'utilisateur dont on veut supprimer la relation
    current_user: Annotated[models.Utilisateur, Depends(get_utilisateur_courant)],
    db: Session = Depends(get_db)  # Dépendance pour obtenir la session de base de données
):
    try:
        # Vérifie que la requête est soit appelée par un admin de la classe, soit par l'utilisateur concerné
        admin_of_class = db.query(models.UtilisateurGroupe).filter(
            (models.UtilisateurGroupe.id_groupe == id_groupe) &
            (models.UtilisateurGroupe.pseudo_utilisateur == current_user.pseudo) &
            (models.UtilisateurGroupe.est_admin == True)
        ).first()

        if admin_of_class or pseudo_utilisateur == current_user.pseudo:
            # Chercher la relation entre l'utilisateur et le groupe
            relation = db.query(models.UtilisateurGroupe).filter(
                models.UtilisateurGroupe.id_groupe == id_groupe,
                models.UtilisateurGroupe.pseudo_utilisateur == pseudo_utilisateur
            ).first()
            
            # Vérifier si la relation existe
            if not relation:
                raise HTTPException(status_code=404, detail="L'utilisateur n'est pas membre de cette classe")
            
            # Supprimer la relation utilisateur-groupe
            db.delete(relation)
            db.commit()  # Commit après suppression de la relation
            
            # Vérifier le nombre d'administrateurs restants dans le groupe
            admin_count = get_admin_count(id_groupe=id_groupe, db=db)
            
            if admin_count <= 0:
                groupe = db.query(models.Groupe).filter(models.Groupe.id_groupe == id_groupe).first()
                if groupe:
                    db.delete(groupe)  # Supprimer le groupe si plus d'administrateurs
                    db.commit()  # Commit après suppression du groupe
            
            # Retourner une réponse avec statut 200 OK
            return {"detail": "Relation supprimée avec succès."}
        else:
            raise HTTPException(status_code=403, detail="Accès restreint: Vous n'avez pas l'authorisation de modifier les informations de cet utilisateur pour cette classe")
    
    except HTTPException as e:
        raise e  # Relever les exceptions HTTP comme 404

    except Exception as e:
        # Gestion des erreurs générales
        raise HTTPException(status_code=500, detail=f"Erreur lors de la suppression de la relation : {str(e)}")


@app.post("/exercice_groupe/",response_model=ExerciceGroupeModel)
async def ajouter_exercice_groupe(ex_data : ExerciceGroupeBase,db: Session = Depends(get_db)):
    #créer nouveau exercice dans table Exercice (commun aux exerices dans la partie apprendre)
    new_exo = models.ExerciceGroupe(
        id_exercice = ex_data.id_exercice,
        id_groupe = ex_data.id_groupe
    )
    
    try:
        db.add(new_exo)
        db.commit()
        db.refresh(new_exo)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur pendant l'ajout d'un exercice de groupe: {str(e)}")
    
    

    return new_exo


@app.get("/exercice_groupe/", response_model=List[ExerciceGroupeModel])
async def lire_tous_exercice_groupe(db: Session = Depends(get_db), skip: int = 0, limit: int = 100):
    e = db.query(models.ExerciceGroupe).offset(skip).limit(limit).all()
    return e

#retourne tous les exercices liés au groupe
@app.get("/exercice_groupe/{id_groupe}",response_model=List[ExerciceModele])
async def lire_exercice_groupe(id_groupe: int, db: Session = Depends(get_db)):
    e = (
        db.query(models.Exercice).join(models.ExerciceGroupe,models.ExerciceGroupe.id_exercice == models.Exercice.id_exercice)
        .filter(models.ExerciceGroupe.id_groupe == id_groupe).all()
    )

    if not e:
        return Response(status_code=204)
    return e

@app.delete("/exercice_groupe/{id_groupe}/{id_exercice}")
async def supprimer_exercice_groupe(id_groupe: int,id_exercice: int, db: Session = Depends(get_db)):
    #Supprime uniquement la liaison et non l'exercice lui même, ceci est fait par le front en appelant le delete d'exercice
    db_liaison = db.query(models.ExerciceGroupe).filter(models.ExerciceGroupe.id_groupe == id_groupe,
    models.ExerciceGroupe.id_exercice == id_exercice).first()
    if not db_liaison:
        raise HTTPException(status_code=404, detail="Laision Exercice-Groupe non trouvé")
    
    id_e = db_liaison.id_exercice
    id_g = db_liaison.id_groupe

    db.delete(db_liaison)
    db.commit()

    return {"message": f"laison Exercice-Groupe {id_e}-{id_g} supprimée avec succès."}


# Badges
@app.post("/badges/", response_model=BadgeModele)
async def add_badge(badge_data: BadgeBase, db: Session = Depends(get_db)):
    # Crée le nouveau badge
    new_badge = models.Badge(
        titre_badge = badge_data.titre_badge,
        description_badge = badge_data.description_badge,
        image_badge = badge_data.image_badge
    )

    try:
        db.add(new_badge)
        db.commit()
        db.refresh(new_badge)
    except Exception as e:
        db.rollback()  # Rollback en cas d'erreur
        raise HTTPException(status_code=500, detail=f"Erreur pendant l'ajout du badge: {str(e)}")
    
    return new_badge

@app.post("/gain_badge")
async def ajout_gain_badge(pseudo_utilisateur: str, id_badge: int, db: Session = Depends(get_db)):
    # Vérifier si l'utilisateur a déjà ce badge
    utilisateur_badge = db.query(models.UtilisateurBadge).filter(
        models.UtilisateurBadge.pseudo_utilisateur == pseudo_utilisateur,
        models.UtilisateurBadge.id_badge == id_badge
    ).first()

    if utilisateur_badge:
        # Si l'utilisateur a déjà ce badge
        return Response(status_code=204)
    
    try:
        # Ajouter un nouveau badge pour l'utilisateur
        new_gain = models.UtilisateurBadge(
            pseudo_utilisateur=pseudo_utilisateur,
            id_badge=id_badge
        )
        db.add(new_gain)
        db.commit()
        db.refresh(new_gain)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur pendant l'ajout du gain de badge: {str(e)}")

    return {"message": "Badge ajouté avec succès", "badge": new_gain}


@app.delete("/gain_badge/{pseudo_utilisateur}")
async def supprimer_tous_les_badges(
    pseudo_utilisateur: str,
    db: Session = Depends(get_db)
):
    # Vérifier si l'utilisateur a des badges
    gains = db.query(models.UtilisateurBadge).filter(
        models.UtilisateurBadge.pseudo_utilisateur == pseudo_utilisateur
    ).all()

    if not gains:
        raise HTTPException(status_code=404, detail="Aucun badge trouvé pour cet utilisateur.")

    # Supprimer tous les badges de l'utilisateur
    try:
        db.query(models.UtilisateurBadge).filter(
            models.UtilisateurBadge.pseudo_utilisateur == pseudo_utilisateur
        ).delete()

        db.commit()
    except Exception as e:
        db.rollback()  # Rollback en cas d'erreur
        raise HTTPException(status_code=500, detail=f"Erreur pendant la suppression des badges: {str(e)}")

    return {"message": "Tous les badges ont été supprimés avec succès."}  # Retour d'un message de succès


@app.get("/badge/{pseudo}", response_model=List[BadgeModele])
async def lire_ses_badges(
    pseudo: str,
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
):
    # Query only badges belonging to the authenticated user
    infos_badges = db.query(models.UtilisateurBadge).filter(models.UtilisateurBadge.pseudo_utilisateur == pseudo).offset(skip).limit(limit).all()
    # Serialize database models into Pydantic models
    
    badges = [db.query(models.Badge).filter(models.Badge.id_badge == badge.id_badge).first() for badge in infos_badges]
    badges = [badge for badge in badges if badge is not None]  # Enlever les valeurs None
    return badges

@app.get("/badge_manquant/{id_badge}", response_model=BadgeModele)
async def recuperer_badge_par_id(
    id_badge: int,
    db: Session = Depends(get_db)
):
    # Rechercher le badge avec l'id donné
    badge = db.query(models.Badge).filter(models.Badge.id_badge == id_badge).first()
    
    if not badge:
        raise HTTPException(status_code=404, detail="Badge introuvable.")
    
    return badge

@app.get("/badge_membres/{id_badge}", response_model=List[UtilisateurRenvoye])
async def recuperer_membres_badge(
    id_badge: int,
    db: Session = Depends(get_db)
):
    try:
        affiliations = db.query(models.UtilisateurBadge).filter(models.UtilisateurBadge.id_badge == id_badge).all()

        if not affiliations:
            return Response(status_code=204)
        utilisateurs = [db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == affiliation.pseudo_utilisateur).first() for affiliation in affiliations]

        utilisateurs = [UtilisateurRenvoye(pseudo=user.pseudo, nom=user.nom, prenom=user.prenom) for user in utilisateurs]
        return utilisateurs

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des membres du badge : {str(e)}")  
   

def attribuer_badges_classement(idDefi, db):
    try:
        reussites_defi = db.query(models.UtilisateurDefi).filter(models.UtilisateurDefi.id_defi == idDefi).all()
        if not reussites_defi:
            print(f"Aucune réussite trouvée pour le défi {idDefi}")
            return
        
        reussites_defi_triees = sorted(reussites_defi, key=lambda x: x.temps_reussite)
        utilisateurs_traites = set()
        position = 0
                
        for reussite in reussites_defi_triees:
            pseudo = reussite.pseudo_utilisateur
            
            if pseudo in utilisateurs_traites:
                continue
                
            position += 1
            utilisateurs_traites.add(pseudo)
            if position > 10:
                break
            
            # Récupérer les badges actuels de l'utilisateur
            badges_utilisateur = db.query(models.UtilisateurBadge).filter(models.UtilisateurBadge.pseudo_utilisateur == pseudo).all()
            badge_ids_utilisateur = [badge.id_badge for badge in badges_utilisateur]
            
            if position == 1 and 3 not in badge_ids_utilisateur:
                new_badge = models.UtilisateurBadge(pseudo_utilisateur=pseudo, id_badge=3)
                db.add(new_badge)
            if position <= 5 and 2 not in badge_ids_utilisateur:
                new_badge = models.UtilisateurBadge(pseudo_utilisateur=pseudo, id_badge=2)
                db.add(new_badge)
            if position <= 10 and 1 not in badge_ids_utilisateur:
                new_badge = models.UtilisateurBadge(pseudo_utilisateur=pseudo, id_badge=1)
                db.add(new_badge)

        if position == 0:
            return    
        db.commit()

    except Exception as e:
        db.rollback()
        print(f"Erreur lors de l'attribution des badges : {str(e)}")


# Créer un exercice
@app.post('/exercices/', response_model=ExerciceModele)
async def creer_exercice(exercice: ExerciceBase, db: Session = Depends(get_db)):
    try:
        db_exercice = models.Exercice(**exercice.dict())
        db.add(db_exercice)
        db.commit()
        db.refresh(db_exercice)
        return db_exercice
    except Exception as e:
        db.rollback()  # Rollback la transaction en cas d'erreur
        raise HTTPException(status_code=500, detail=f"Erreur lors de la création de l'exercice: {str(e)}")

# Lire tous les exercices
@app.get('/exercices/', response_model=List[ExerciceModele])
async def lire_exercices(db: Session = Depends(get_db), skip: int = 0, limit: int = 100):
    try:
        exercices = db.query(models.Exercice).offset(skip).limit(limit).all()
        if not exercices:
            raise HTTPException(status_code=404, detail="Aucun exercice trouvé")
        return exercices
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des exercices: {str(e)}")

# Lire un exercice par ID
@app.get('/exercices/{id_exercice}', response_model=ExerciceModele)
async def lire_exercice_par_id(id_exercice: int, db: Session = Depends(get_db)):
    try:
        exercice = db.query(models.Exercice).filter(models.Exercice.id_exercice == id_exercice).first()
        if not exercice:
            raise HTTPException(status_code=404, detail="Exercice non trouvé")
        return exercice
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération de l'exercice: {str(e)}")

# Supprimer un exercice
@app.delete('/exercices/{id_exercice}', response_model=dict)
async def supprimer_exercice(id_exercice: int, db: Session = Depends(get_db)):
    try:
        exercice = db.query(models.Exercice).filter(models.Exercice.id_exercice == id_exercice).first()
        if not exercice:
            raise HTTPException(status_code=404, detail="Exercice non trouvé")
        
        db.delete(exercice)
        db.commit()
        return {"message": f"Exercice avec l'ID '{id_exercice}' supprimé avec succès."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la suppression de l'exercice: {str(e)}")
    

@app.post('/exercices_realises/', response_model=ExerciceUtilisateurModele)
async def ajouter_exercice_realise(
    id_exercice: int,  # ID de l'exercice
    pseudo: str,  # Pseudo de l'utilisateur
    db: Session = Depends(get_db)  # Session de base de données
):
    try:
        # Vérifier si l'utilisateur existe
        db_utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not db_utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
        
        # Vérifier si l'exercice existe
        db_exercice = db.query(models.Exercice).filter(models.Exercice.id_exercice == id_exercice).first()
        if not db_exercice:
            raise HTTPException(status_code=404, detail="Exercice non trouvé")

        # Vérifier si l'association existe déjà
        relation_existante = db.query(models.ExerciceUtilisateur).filter(
            models.ExerciceUtilisateur.id_exercice == id_exercice,
            models.ExerciceUtilisateur.pseudo == pseudo
        ).first()
        if relation_existante:
            return Response(status_code=204)

        # Ajouter l'association
        exercice_realise = models.ExerciceUtilisateur(
            id_exercice=id_exercice,
            pseudo=pseudo,
            exercice_fait=True  # Marquer comme réalisé
        )
        db.add(exercice_realise)
        db.commit()
        db.refresh(exercice_realise)
        return exercice_realise
    
    except HTTPException as e:
        raise e

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")


@app.get('/exercices_realises/{pseudo}', response_model=List[ExerciceUtilisateurModele])
async def lire_exercices_realises(
    pseudo: str,  # Pseudo de l'utilisateur
    db: Session = Depends(get_db),  # Session de base de données
    skip: int = 0,  # Pagination : décalage
    limit: int = 100  # Pagination : limite
):
    try:
        # Vérifier si l'utilisateur existe
        db_utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.pseudo == pseudo).first()
        if not db_utilisateur:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        # Récupérer les exercices réalisés
        exercices = db.query(models.ExerciceUtilisateur).filter(
            models.ExerciceUtilisateur.pseudo == pseudo,
            models.ExerciceUtilisateur.exercice_fait == True
        ).offset(skip).limit(limit).all()

        if not exercices:
            return Response(status_code=204)
        
        return exercices
    
    except HTTPException as e:
        raise e
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")


@app.delete('/exercices_realises/', response_model=dict)
async def supprimer_exercice_realise(
    id_exercice: int,  # ID de l'exercice
    pseudo: str,  # Pseudo de l'utilisateur
    db: Session = Depends(get_db)  # Session de base de données
):
    try:
        # Vérifier si la relation existe
        relation = db.query(models.ExerciceUtilisateur).filter(
            models.ExerciceUtilisateur.id_exercice == id_exercice,
            models.ExerciceUtilisateur.pseudo == pseudo
        ).first()
        
        if not relation:
            return Response(status_code=204)

        # Supprimer la relation
        db.delete(relation)
        db.commit()

        return {"message": f"L'exercice avec ID {id_exercice} pour l'utilisateur '{pseudo}' a été supprimé avec succès."}
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")

@app.post('/stat/', response_model=StatsUtilisateur)
async def ajouter_stat(
    pseudo_utilisateur: str,
    type_stat: str,
    valeur_stat: float,
    db: Session = Depends(get_db)
):
    try:
        db_stat = models.Stat(
            pseudo_utilisateur=pseudo_utilisateur,
            type_stat=type_stat,
            valeur_stat=valeur_stat,
            date_stat=int(time.time())
        )
        
        utilisateur_db = get_utilisateur(db, db_stat.pseudo_utilisateur)

        if not utilisateur_db:
            raise HTTPException(status_code=404, detail="Aucun utilisateur trouvé")

        db.add(db_stat)
        db.commit()
        db.refresh(db_stat)
        return db_stat
    
    except HTTPException as e:
        raise e
    
    except Exception as e:
        db.rollback()  # Rollback the transaction if an error occurs
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'ajout de la stat : {str(e)}")
    
@app.get('/stat/', response_model=List[StatsUtilisateur])
async def lire_stats_utilisateur(
    pseudo_utilisateur: str,
    type_stat: str,
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 200
):
    stats = db.query(models.Stat).filter(models.Stat.pseudo_utilisateur == pseudo_utilisateur, models.Stat.type_stat == type_stat).offset(skip).limit(limit).all()
    return stats


@app.get("/defi_semaine")
def get_defi_semaine(db: Session = Depends(get_db)):
    try:
        defi_semaine = db.query(models.DefiSemaine).first()
        
        if not defi_semaine:
            # Créer le premier défi s'il n'existe pas
            defi_semaine = models.DefiSemaine(numero_defi=1)
            db.add(defi_semaine)
            db.commit()
            
        return {"numero_defi": defi_semaine.numero_defi}  # Retourner un dict JSON valide
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la récupération du numéro de défi : {str(e)}"
        )
    
@app.get("/photo_profil", response_model=list[ProfilePicture])
def get_profile_pictures(db: Session = Depends(get_db)):
    pictures = db.query(models.ProfilePicture).all()
    return pictures

@app.get("/photo_profil/{id_photo}", response_model=ProfilePicture)
def get_profile_picture_id(id_photo: int,db: Session = Depends(get_db)):
    photo = db.query(models.ProfilePicture).filter(models.ProfilePicture.id_photo == id_photo).first()
    return photo
